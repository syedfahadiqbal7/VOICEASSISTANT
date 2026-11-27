import 'dart:async';
import 'dart:convert';

import 'package:cryptography/cryptography.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'mobile_identity_service.dart';
import 'signaling_envelope.dart';

const String _relayAudience = 'voiceassistant-relay';
const int _maxRelayFrameBytes = 64 * 1024;

class MobileRelayException implements Exception {
  const MobileRelayException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Authenticated mobile-to-relay signaling socket. It does not establish
/// WebRTC, authorize commands, or replace the existing direct desktop socket.
class MobileRelayConnection {
  MobileRelayConnection._(this._channel);

  final WebSocketChannel _channel;
  final StreamController<Map<String, dynamic>> _frames =
      StreamController<Map<String, dynamic>>.broadcast();

  Stream<Map<String, dynamic>> get frames => _frames.stream;
  bool _closed = false;
  StreamSubscription<dynamic>? _subscription;

  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await _subscription?.cancel();
    await _channel.sink.close();
    if (!_frames.isClosed) await _frames.close();
  }

  static const FlutterSecureStorage _storage = FlutterSecureStorage();
  static MobileRelayConnection? active;

  static Future<void> disconnect() async {
    final current = active;
    active = null;
    await current?.close();
  }

  /// Strict structural validation, also used by unit tests before any network
  /// operation is attempted.
  static Map<String, dynamic> parsePayload(String raw) {
    if (raw.length > 16384) {
      throw const MobileRelayException('Relay QR exceeds its size limit');
    }
    dynamic value;
    try {
      value = jsonDecode(raw);
    } catch (_) {
      throw const MobileRelayException('Relay QR is not valid JSON');
    }
    const fields = {
      'type', 'version', 'relay_url', 'grant', 'grant_signature',
      'desktop_identity', 'mobile_identity',
    };
    if (value is! Map<String, dynamic> ||
        value.keys.toSet().difference(fields).isNotEmpty ||
        value.keys.toSet().length != fields.length ||
        value['type'] != 'voiceassistant_relay_session' ||
        value['version'] != 1) {
      throw const MobileRelayException('Unsupported relay QR format');
    }
    final uri = Uri.tryParse(value['relay_url'] is String ? value['relay_url'] as String : '');
    if (uri == null ||
        uri.scheme != 'wss' ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment ||
        (uri.path.isNotEmpty && uri.path != '/') ||
        ![null, 443, 8443].contains(uri.port == 443 ? 443 : uri.port)) {
      throw const MobileRelayException('Relay endpoint must be a trusted WSS origin');
    }
    final grant = value['grant'];
    const grantFields = {
      'protocol_version', 'audience', 'session_id', 'desktop_id', 'mobile_id',
      'desktop_public_key', 'mobile_public_key', 'issued_at_ms', 'expires_at_ms',
    };
    if (grant is! Map<String, dynamic> ||
        grant.keys.toSet().difference(grantFields).isNotEmpty ||
        grant.keys.toSet().length != grantFields.length ||
        grant['protocol_version'] != 1 ||
        grant['audience'] != _relayAudience ||
        !_id(grant['session_id']) ||
        !_id(grant['desktop_id']) ||
        !_id(grant['mobile_id']) ||
        grant['issued_at_ms'] is! int ||
        grant['expires_at_ms'] is! int) {
      throw const MobileRelayException('Relay grant fields are invalid');
    }
    final now = DateTime.now().millisecondsSinceEpoch;
    final issued = grant['issued_at_ms'] as int;
    final expires = grant['expires_at_ms'] as int;
    if (issued > now + 30000 || expires <= now ||
        expires <= issued || expires - issued > 900000) {
      throw const MobileRelayException('Relay grant is expired or has an invalid lifetime');
    }
    final signature = value['grant_signature'];
    if (signature is! String ||
        signature.length != 86 ||
        !RegExp(r'^[A-Za-z0-9_-]{86}$').hasMatch(signature)) {
      throw const MobileRelayException('Relay grant signature is malformed');
    }
    final desktop = value['desktop_identity'];
    final mobile = value['mobile_identity'];
    if (desktop is! Map<String, dynamic> ||
        mobile is! Map<String, dynamic> ||
        desktop.keys.toSet().difference({
          'identity_id', 'algorithm', 'public_key', 'fingerprint_sha256',
        }).isNotEmpty ||
        desktop.keys.toSet().length != 4 ||
        mobile.keys.toSet().difference({
          'device_id', 'algorithm', 'fingerprint_sha256',
        }).isNotEmpty ||
        mobile.keys.toSet().length != 3 ||
        desktop['identity_id'] != grant['desktop_id'] ||
        desktop['algorithm'] != 'Ed25519' ||
        desktop['public_key'] != grant['desktop_public_key'] ||
        desktop['fingerprint_sha256'] is! String ||
        mobile['device_id'] != grant['mobile_id'] ||
        mobile['algorithm'] != 'Ed25519' ||
        mobile['fingerprint_sha256'] is! String) {
      throw const MobileRelayException('Relay identity metadata does not match the grant');
    }
    return value;
  }

  static bool _id(dynamic value) =>
      value is String && RegExp(r'^[0-9a-f]{32}$').hasMatch(value);

  static String _fingerprint(List<int> bytes) {
    return bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }

  static List<int> _decodeKey(dynamic value, String label) {
    if (value is! String || value.length != 43 ||
        !RegExp(r'^[A-Za-z0-9_-]{43}$').hasMatch(value)) {
      throw MobileRelayException('$label public key is malformed');
    }
    try {
      final bytes = base64Url.decode('$value=');
      if (bytes.length != 32 ||
          base64Url.encode(bytes).replaceAll('=', '') != value) {
        throw const FormatException();
      }
      return bytes;
    } catch (_) {
      throw MobileRelayException('$label public key is not canonical Ed25519 base64url');
    }
  }

  static List<int> _decodeSignature(String value) {
    try {
      final bytes = base64Url.decode('$value==');
      if (bytes.length != 64 ||
          base64Url.encode(bytes).replaceAll('=', '') != value) {
        throw const FormatException();
      }
      return bytes;
    } catch (_) {
      throw const MobileRelayException('Relay grant signature is invalid');
    }
  }

  static Future<MobileRelayConnection> connectFromQr(String raw) async {
    final payload = parsePayload(raw);
    final grant = Map<String, dynamic>.from(payload['grant'] as Map);
    final desktop = Map<String, dynamic>.from(payload['desktop_identity'] as Map);
    final mobile = Map<String, dynamic>.from(payload['mobile_identity'] as Map);
    final desktopKey = _decodeKey(grant['desktop_public_key'], 'Desktop');
    final mobileKey = _decodeKey(grant['mobile_public_key'], 'Mobile');
    final desktopFingerprint = _fingerprint((await Sha256().hash(desktopKey)).bytes);
    final mobileFingerprint = _fingerprint((await Sha256().hash(mobileKey)).bytes);
    if (desktop['public_key'] != grant['desktop_public_key'] ||
        desktop['fingerprint_sha256'] != desktopFingerprint ||
        mobile['fingerprint_sha256'] != mobileFingerprint) {
      throw const MobileRelayException('Relay identity fingerprint does not match its key');
    }

    final signature = _decodeSignature(payload['grant_signature'] as String);
    if (!await MobileIdentityService.verify(
      publicKey: grant['desktop_public_key'] as String,
      message: canonicalSignalingBytes(grant),
      signature: signature,
    )) {
      throw const MobileRelayException('Desktop relay grant signature is invalid');
    }

    final identity = await MobileIdentityService().load();
    if (identity == null ||
        identity.publicKey != grant['mobile_public_key'] ||
        identity.fingerprintSha256 != mobileFingerprint) {
      throw const MobileRelayException(
        'This phone identity does not match the enrolled mobile key in this QR',
      );
    }
    final savedDeviceId = await _storage.read(key: 'mobile_device_id');
    if (savedDeviceId == null || savedDeviceId != grant['mobile_id']) {
      throw const MobileRelayException('This relay QR belongs to a different paired phone');
    }

    final pinnedId = await _storage.read(key: 'desktop_identity_id');
    if (pinnedId == desktop['identity_id']) {
      final pinnedKey = await _storage.read(key: 'desktop_identity_public_key');
      final pinnedFingerprint = await _storage.read(key: 'desktop_identity_fingerprint');
      if (pinnedKey != desktop['public_key'] ||
          pinnedFingerprint != desktopFingerprint) {
        throw const MobileRelayException('Pinned desktop identity changed; relay connection blocked');
      }
    }

    final relayUrl = Uri.parse(payload['relay_url'] as String);
    final socketUri = relayUrl.replace(
      path: '/v1/relay/${grant['session_id']}/mobile',
    );
    final channel = IOWebSocketChannel.connect(
      socketUri,
      connectTimeout: const Duration(seconds: 10),
    );
    final connection = MobileRelayConnection._(channel);
    final ready = Completer<void>();
    var authSent = false;
    final subscription = channel.stream.listen((dynamic raw) async {
      try {
        if (raw is! String || raw.length > 4096) {
          throw const MobileRelayException('Relay handshake frame is invalid');
        }
        final frame = jsonDecode(raw);
        if (frame is! Map<String, dynamic>) {
          throw const MobileRelayException('Relay handshake frame is malformed');
        }
        if (ready.isCompleted) {
          connection._frames.add(frame);
          return;
        }
        if (!authSent) {
          if (frame.keys.toSet().difference({'type', 'challenge', 'protocol_version'}).isNotEmpty ||
              frame.length != 3 ||
              frame['type'] != 'relay.challenge' ||
              frame['protocol_version'] != 1 ||
              frame['challenge'] is! String ||
              !RegExp(r'^[0-9a-f]{64}$').hasMatch(frame['challenge'] as String)) {
            throw const MobileRelayException('Relay challenge does not match protocol v1');
          }
          final issuedAt = DateTime.now().millisecondsSinceEpoch;
          final proof = {
            'protocol_version': 1,
            'session_id': grant['session_id'],
            'side': 'mobile',
            'challenge': frame['challenge'],
            'grant_sha256': _fingerprint((await Sha256().hash(canonicalSignalingBytes(grant))).bytes),
            'issued_at_ms': issuedAt,
          };
          final proofSignature = base64Url.encode(
            await MobileIdentityService().sign(canonicalSignalingBytes(proof)),
          ).replaceAll('=', '');
          channel.sink.add(jsonEncode({
            'type': 'relay.authenticate',
            'grant': grant,
            'grant_signature': payload['grant_signature'],
            'issued_at_ms': issuedAt,
            'proof_signature': proofSignature,
          }));
          authSent = true;
          return;
        }
        if (frame['type'] == 'relay.peer_ready' &&
            frame['protocol_version'] == 1 &&
            frame.length == 2) {
          if (!ready.isCompleted) ready.complete();
        } else if (frame['type'] == 'relay.error') {
          throw MobileRelayException(frame['message']?.toString() ?? 'Relay rejected the session');
        }
      } catch (error, stack) {
        if (!ready.isCompleted) ready.completeError(error, stack);
      }
    }, onError: (Object error, StackTrace stack) {
      if (!ready.isCompleted) ready.completeError(error, stack);
    }, onDone: () {
      if (!ready.isCompleted) {
        ready.completeError(const MobileRelayException('Relay closed before peer authentication'));
      } else if (!connection._frames.isClosed) {
        connection._frames.close();
      }
    });

    connection._subscription = subscription;
    try {
      await ready.future.timeout(const Duration(seconds: 125));
      await _storage.write(key: 'desktop_identity_id', value: desktop['identity_id'] as String);
      await _storage.write(key: 'desktop_identity_public_key', value: desktop['public_key'] as String);
      await _storage.write(key: 'desktop_identity_fingerprint', value: desktopFingerprint);
      // The authenticated socket remains open for the next signed signaling stage.
      await disconnect();
      active = connection;
      return connection;
    } catch (_) {
      await subscription.cancel();
      await channel.sink.close();
      rethrow;
    }
  }
}
