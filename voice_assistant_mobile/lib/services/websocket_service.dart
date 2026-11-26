import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:cryptography/cryptography.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/io.dart';

import 'mobile_identity_service.dart';
import 'signaling_envelope.dart';

class WebSocketService extends ChangeNotifier {
  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  Timer? _connectTimeout;
  Timer? _reconnectTimer;
  final FlutterSecureStorage _secureStorage = const FlutterSecureStorage();

  int _connectionGeneration = 0;
  int _reconnectAttempts = 0;
  bool _manualDisconnect = true;
  String? _lastHost;
  String _lastToken = "";
  String _lastDeviceId = "";
  String _lastRemoteSessionId = "";
  String _lastRemoteSessionTicket = "";
  int _lastRemoteSessionExpiresAtMs = 0;
  int _lastPort = 8765;

  bool isConnected = false;
  bool isConnecting = false;
  bool isPairing = false;
  bool hasSavedConnection = false;
  String statusMessage = "Disconnected";
  String statusColor = "#E74C3C";
  String? lastError;
  String? securityWarning;
  String? lastUndoMessage;

  // Security Gate
  bool showSecurityGate = false;
  String securityText = "";
  String securityRisk = "low";
  String? securityRequestId;
  bool approvalSubmitting = false;
  Timer? _approvalTimeout;

  // Chat messages
  final List<Map<String, dynamic>> messages = [];

  // Session state (desktop → mobile)
  Map<String, dynamic> sessionState = {};
  bool undoAvailable = false;
  int jobsCount = 0;
  List<Map<String, dynamic>> jobs = [];
  List<Map<String, dynamic>> scheduleHistory = [];
  String? lastUserText;
  String? lastStatus;
  Map<String, dynamic>? activePlan;

  Function(String)? onSpeak;

  Future<Map<String, String>?> loadSavedConnection() async {
    try {
      final host = await _secureStorage.read(key: "desktop_host");
      if (host == null || host.trim().isEmpty) {
        hasSavedConnection = false;
        notifyListeners();
        return null;
      }
      final token = await _secureStorage.read(key: "mobile_token") ?? "";
      final deviceId = await _secureStorage.read(key: "mobile_device_id") ?? "";
      final remoteSessionId = await _secureStorage.read(key: "remote_session_id") ?? "";
      final remoteSessionTicket = await _secureStorage.read(key: "remote_session_ticket") ?? "";
      final remoteExpiry = await _secureStorage.read(key: "remote_session_expires_at_ms") ?? "";
      final autoConnect = await _secureStorage.read(key: "auto_connect") ?? "true";
      if (remoteSessionId.isNotEmpty || remoteSessionTicket.isNotEmpty) {
        final expiresAt = int.tryParse(remoteExpiry);
        if (remoteSessionId.isEmpty ||
            remoteSessionTicket.isEmpty ||
            expiresAt == null ||
            expiresAt <= DateTime.now().millisecondsSinceEpoch) {
          await _secureStorage.delete(key: "remote_session_id");
          await _secureStorage.delete(key: "remote_session_ticket");
          await _secureStorage.delete(key: "remote_session_expires_at_ms");
          await _secureStorage.write(key: "auto_connect", value: "false");
          hasSavedConnection = false;
          lastError = "Remote session expired. Scan a fresh desktop QR code.";
          notifyListeners();
          return null;
        }
      }
      hasSavedConnection = true;
      notifyListeners();
      return {
        "host": host,
        "token": token,
        "device_id": deviceId,
        "remote_session_id": remoteSessionId,
        "remote_session_ticket": remoteSessionTicket,
        "remote_session_expires_at_ms": remoteExpiry,
        "auto_connect": autoConnect,
      };
    } catch (error) {
      lastError = "Could not read secure connection settings";
      debugPrint("Secure connection settings read failed: $error");
      notifyListeners();
      return null;
    }
  }

  Future<void> _persistConnection(int generation) async {
    final host = _lastHost;
    if (host == null ||
        host.isEmpty ||
        generation != _connectionGeneration ||
        _manualDisconnect) {
      return;
    }
    try {
      await _secureStorage.write(key: "desktop_host", value: host);
      if (generation != _connectionGeneration || _manualDisconnect) return;
      await _secureStorage.write(key: "mobile_token", value: _lastToken);
      if (generation != _connectionGeneration || _manualDisconnect) return;
      await _secureStorage.write(key: "mobile_device_id", value: _lastDeviceId);
      if (generation != _connectionGeneration || _manualDisconnect) return;
      if (_lastRemoteSessionId.isNotEmpty && _lastRemoteSessionTicket.isNotEmpty) {
        await _secureStorage.write(key: "remote_session_id", value: _lastRemoteSessionId);
        await _secureStorage.write(key: "remote_session_ticket", value: _lastRemoteSessionTicket);
        await _secureStorage.write(
          key: "remote_session_expires_at_ms",
          value: _lastRemoteSessionExpiresAtMs.toString(),
        );
      } else {
        await _secureStorage.delete(key: "remote_session_id");
        await _secureStorage.delete(key: "remote_session_ticket");
        await _secureStorage.delete(key: "remote_session_expires_at_ms");
      }
      if (generation != _connectionGeneration || _manualDisconnect) return;
      await _secureStorage.write(key: "auto_connect", value: "true");
      if (generation != _connectionGeneration || _manualDisconnect) return;
      hasSavedConnection = true;
    } catch (error) {
      if (generation == _connectionGeneration && !_manualDisconnect) {
        lastError = "Connection works, but secure credential storage failed";
        debugPrint("Secure connection settings write failed: $error");
      }
    }
    if (generation == _connectionGeneration && !_manualDisconnect) {
      notifyListeners();
    }
  }

  Future<void> _setAutoConnect(bool enabled) async {
    try {
      await _secureStorage.write(key: "auto_connect", value: enabled ? "true" : "false");
    } catch (error) {
      debugPrint("Auto-connect preference update failed: $error");
    }
  }

  Future<void> _clearRemoteSessionCredentials() async {
    _lastRemoteSessionId = "";
    _lastRemoteSessionTicket = "";
    _lastRemoteSessionExpiresAtMs = 0;
    try {
      await _secureStorage.delete(key: "remote_session_id");
      await _secureStorage.delete(key: "remote_session_ticket");
      await _secureStorage.delete(key: "remote_session_expires_at_ms");
      await _secureStorage.write(key: "auto_connect", value: "false");
    } catch (error) {
      debugPrint("Remote session credential cleanup failed: $error");
    }
  }

  Future<void> forgetSavedConnection() async {
    // Forgetting credentials must also stop the live socket and its reconnect loop.
    _manualDisconnect = true;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    ++_connectionGeneration;
    _connectTimeout?.cancel();
    _connectTimeout = null;
    _approvalTimeout?.cancel();
    _approvalTimeout = null;

    try {
      await _sub?.cancel();
    } catch (_) {}
    _sub = null;
    try {
      await _channel?.sink.close();
    } catch (_) {}
    _channel = null;

    isConnected = false;
    isConnecting = false;
    showSecurityGate = false;
    securityText = "";
    securityRequestId = null;
    approvalSubmitting = false;
    statusMessage = "Disconnected";
    statusColor = "#E74C3C";
    _lastHost = null;
    _lastToken = "";
    _lastDeviceId = "";
    _lastRemoteSessionId = "";
    _lastRemoteSessionTicket = "";
    _lastRemoteSessionExpiresAtMs = 0;
    _lastPort = 8765;

    try {
      await _secureStorage.delete(key: "desktop_host");
      await _secureStorage.delete(key: "mobile_token");
      await _secureStorage.delete(key: "mobile_device_id");
      await _secureStorage.delete(key: "remote_session_id");
      await _secureStorage.delete(key: "remote_session_ticket");
      await _secureStorage.delete(key: "remote_session_expires_at_ms");
      await _secureStorage.delete(key: "auto_connect");
      hasSavedConnection = false;
      lastError = null;
    } catch (error) {
      lastError = "Connection stopped, but saved credentials could not be removed";
      debugPrint("Secure connection settings delete failed: $error");
    }
    notifyListeners();
  }


  static Uri _validatePairingEndpoint(dynamic value) {
    if (value is! String || value.length > 2048) {
      throw const FormatException("Invalid QR pairing endpoint");
    }
    final endpoint = Uri.tryParse(value);
    if (endpoint == null ||
        endpoint.scheme != "https" ||
        endpoint.host.isEmpty ||
        endpoint.userInfo.isNotEmpty ||
        endpoint.hasQuery ||
        endpoint.hasFragment ||
        (endpoint.path.isNotEmpty && endpoint.path != "/")) {
      throw const FormatException("QR pairing requires a trusted HTTPS endpoint");
    }
    return endpoint;
  }

  /// Validate the desktop public identity carried in a QR before the UI
  /// asks the user to compare its fingerprint with the trusted desktop window.
  static Future<Map<String, String>> inspectDesktopIdentity(String rawPayload) async {
    dynamic decoded;
    try {
      decoded = jsonDecode(rawPayload);
    } catch (_) {
      throw const FormatException("QR code is not valid JSON");
    }
    if (decoded is! Map ||
        !((decoded["type"] == "voiceassistant_mobile_pairing" && decoded["version"] == 2) ||
          (decoded["type"] == "voiceassistant_remote_session" && decoded["version"] == 1))) {
      throw const FormatException("This is not a supported VoiceAssistant desktop QR code");
    }
    final identity = decoded["desktop_identity"];
    const identityFields = {
      "identity_id", "algorithm", "public_key", "fingerprint_sha256",
    };
    if (identity is! Map ||
        identity.keys.toSet().difference(identityFields).isNotEmpty ||
        identity.keys.toSet().length != identityFields.length ||
        identity["identity_id"] is! String ||
        !RegExp(r"^[0-9a-f]{32}$").hasMatch(identity["identity_id"] as String) ||
        identity["algorithm"] != "Ed25519" ||
        identity["public_key"] is! String ||
        identity["fingerprint_sha256"] is! String) {
      throw const FormatException("QR code has no valid desktop identity");
    }
    final publicKey = identity["public_key"] as String;
    if (publicKey.length != 43 || publicKey.contains("=")) {
      throw const FormatException("Desktop public key is malformed");
    }
    List<int> publicBytes;
    try {
      publicBytes = base64Url.decode(
        publicKey.padRight(publicKey.length + ((4 - publicKey.length % 4) % 4), "="),
      );
    } catch (_) {
      throw const FormatException("Desktop public key is malformed");
    }
    if (publicBytes.length != 32 ||
        base64Url.encode(publicBytes).replaceAll("=", "") != publicKey) {
      throw const FormatException("Desktop public key is not canonical Ed25519");
    }
    final digest = await Sha256().hash(publicBytes);
    final fingerprint = digest.bytes
        .map((byte) => byte.toRadixString(16).padLeft(2, "0"))
        .join();
    if (identity["fingerprint_sha256"] != fingerprint) {
      throw const FormatException("Desktop identity fingerprint does not match its public key");
    }
    return {
      "identity_id": identity["identity_id"] as String,
      "algorithm": "Ed25519",
      "public_key": publicKey,
      "fingerprint_sha256": fingerprint,
    };
  }

  static Future<Map<String, dynamic>> _postPairingJson(
    HttpClient client,
    Uri endpoint,
    Map<String, dynamic> payload,
  ) async {
    final request = await client.postUrl(endpoint).timeout(const Duration(seconds: 5));
    request.followRedirects = false;
    request.headers.contentType = ContentType.json;
    request.add(utf8.encode(jsonEncode(payload)));
    final response = await request.close().timeout(const Duration(seconds: 5));
    final bytes = await response.fold<List<int>>(<int>[], (buffer, chunk) {
      if (buffer.length + chunk.length > 16384) {
        throw const FormatException("Pairing server response is too large");
      }
      buffer.addAll(chunk);
      return buffer;
    }).timeout(const Duration(seconds: 5));
    final decoded = jsonDecode(utf8.decode(bytes));
    if (decoded is! Map) {
      throw const FormatException("Invalid pairing server response");
    }
    final result = Map<String, dynamic>.from(decoded);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw StateError(result["detail"]?.toString() ?? "Pairing request was rejected");
    }
    return result;
  }

  Future<void> connectRemoteFromQr(String rawPayload) async {
    if (isPairing) return;
    final decoded = jsonDecode(rawPayload);
    if (decoded is! Map ||
        decoded["type"] != "voiceassistant_remote_session" ||
        decoded["version"] != 1) {
      throw const FormatException("This is not a VoiceAssistant remote-session QR code");
    }
    const qrFields = {
      "type", "version", "endpoint", "session_id", "device_id", "challenge",
      "issued_at_ms", "expires_at_ms", "desktop_identity", "grant_signature",
    };
    if (decoded.keys.toSet().difference(qrFields).isNotEmpty ||
        decoded.keys.toSet().length != qrFields.length) {
      throw const FormatException("Remote-session QR contains unsupported fields");
    }
    final endpoint = _validatePairingEndpoint(decoded["endpoint"]);
    final sessionId = decoded["session_id"];
    final deviceId = decoded["device_id"];
    final challenge = decoded["challenge"];
    final issuedAtMs = decoded["issued_at_ms"];
    final expiresAtMs = decoded["expires_at_ms"];
    final grantSignature = decoded["grant_signature"];
    if (sessionId is! String ||
        !RegExp(r"^[0-9a-f]{32}$").hasMatch(sessionId) ||
        deviceId is! String ||
        !RegExp(r"^[0-9a-f]{32}$").hasMatch(deviceId) ||
        challenge is! String ||
        !RegExp(r"^[A-Za-z0-9_-]{40,64}$").hasMatch(challenge) ||
        issuedAtMs is! int ||
        expiresAtMs is! int ||
        grantSignature is! String ||
        grantSignature.length != 86 ||
        !RegExp(r"^[A-Za-z0-9_-]+$").hasMatch(grantSignature)) {
      throw const FormatException("Remote-session QR data is incomplete or invalid");
    }
    final nowMs = DateTime.now().millisecondsSinceEpoch;
    if (issuedAtMs > nowMs + 10000 ||
        nowMs - issuedAtMs > 180000 ||
        expiresAtMs <= nowMs ||
        expiresAtMs <= issuedAtMs ||
        expiresAtMs - issuedAtMs > 180000) {
      throw const FormatException("Remote-session QR has expired or has an invalid lifetime");
    }

    final desktopIdentity = await inspectDesktopIdentity(rawPayload);
    final identityRecord = decoded["desktop_identity"] as Map;
    final grant = {
      "protocol_version": 1,
      "endpoint": endpoint.toString(),
      "session_id": sessionId,
      "device_id": deviceId,
      "challenge": challenge,
      "issued_at_ms": issuedAtMs,
      "expires_at_ms": expiresAtMs,
      "desktop_identity_id": desktopIdentity["identity_id"],
    };
    final signatureBytes = base64Url.decode(
      grantSignature.padRight(grantSignature.length + ((4 - grantSignature.length % 4) % 4), "="),
    );
    if (signatureBytes.length != 64 ||
        base64Url.encode(signatureBytes).replaceAll("=", "") != grantSignature ||
        !await MobileIdentityService.verify(
          publicKey: desktopIdentity["public_key"]!,
          message: canonicalSignalingBytes(grant),
          signature: signatureBytes,
        )) {
      throw const FormatException("Desktop remote-session QR signature is invalid");
    }
    if (identityRecord["identity_id"] != desktopIdentity["identity_id"] ||
        identityRecord["public_key"] != desktopIdentity["public_key"] ||
        identityRecord["fingerprint_sha256"] != desktopIdentity["fingerprint_sha256"]) {
      throw const FormatException("Desktop identity metadata changed during QR validation");
    }

    final pinnedIdentityId = await _secureStorage.read(key: "desktop_identity_id");
    if (pinnedIdentityId == desktopIdentity["identity_id"]) {
      final pinnedPublicKey = await _secureStorage.read(key: "desktop_identity_public_key");
      final pinnedFingerprint = await _secureStorage.read(key: "desktop_identity_fingerprint");
      if (pinnedPublicKey != desktopIdentity["public_key"] ||
          pinnedFingerprint != desktopIdentity["fingerprint_sha256"]) {
        throw StateError("This desktop identity changed unexpectedly. Remote connection is blocked.");
      }
    }

    final mobileIdentity = MobileIdentityService();
    final mobileKey = await mobileIdentity.load();
    if (mobileKey == null) {
      throw StateError("This phone is not paired with the desktop yet. Complete normal QR pairing first.");
    }

    final proofIssuedAtMs = DateTime.now().millisecondsSinceEpoch;
    final proof = {
      "protocol_version": 1,
      "session_id": sessionId,
      "device_id": deviceId,
      "challenge": challenge,
      "issued_at_ms": proofIssuedAtMs,
    };
    final proofSignature = base64Url
        .encode(await mobileIdentity.sign(canonicalSignalingBytes(proof)))
        .replaceAll("=", "");

    final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
    isPairing = true;
    statusMessage = "Verifying signed remote session...";
    statusColor = "#F39C12";
    lastError = null;
    notifyListeners();
    try {
      final result = await _postPairingJson(
        client,
        endpoint.replace(path: "/remote/sessions/redeem"),
        {
          "session_id": sessionId,
          "device_id": deviceId,
          "challenge": challenge,
          "issued_at_ms": proofIssuedAtMs,
          "signature": proofSignature,
        },
      );
      final accessTicket = result["access_ticket"];
      final returnedExpiry = result["expires_at_ms"];
      if (result["ok"] != true ||
          result["session_id"] != sessionId ||
          result["device_id"] != deviceId ||
          accessTicket is! String ||
          accessTicket.length < 32 ||
          accessTicket.length > 128 ||
          returnedExpiry is! int ||
          returnedExpiry <= DateTime.now().millisecondsSinceEpoch ||
          returnedExpiry > DateTime.now().millisecondsSinceEpoch + 900000) {
        throw const FormatException("Desktop returned an invalid remote-session ticket");
      }

      final socketEndpoint = endpoint.replace(scheme: "wss", path: "/ws");
      await _secureStorage.write(key: "desktop_host", value: socketEndpoint.toString());
      await _secureStorage.write(key: "mobile_device_id", value: deviceId);
      await _secureStorage.delete(key: "mobile_token");
      await _secureStorage.write(key: "remote_session_id", value: sessionId);
      await _secureStorage.write(key: "remote_session_ticket", value: accessTicket);
      await _secureStorage.write(key: "remote_session_expires_at_ms", value: returnedExpiry.toString());
      await _secureStorage.write(key: "desktop_identity_id", value: desktopIdentity["identity_id"]!);
      await _secureStorage.write(key: "desktop_identity_public_key", value: desktopIdentity["public_key"]!);
      await _secureStorage.write(key: "desktop_identity_fingerprint", value: desktopIdentity["fingerprint_sha256"]!);
      await _secureStorage.write(key: "auto_connect", value: "true");
      hasSavedConnection = true;
      isPairing = false;
      notifyListeners();
      connect(
        socketEndpoint.toString(),
        deviceId: deviceId,
        remoteSessionId: sessionId,
        remoteSessionTicket: accessTicket,
        remoteSessionExpiresAtMs: returnedExpiry,
      );
    } catch (error) {
      isPairing = false;
      statusMessage = "Remote connection failed";
      statusColor = "#E74C3C";
      lastError = error.toString();
      notifyListeners();
      rethrow;
    } finally {
      client.close(force: true);
      if (isPairing) {
        isPairing = false;
        notifyListeners();
      }
    }
  }

  Future<void> pairFromQr(String rawPayload, {required String deviceName}) async {
    if (isPairing) return;
    if (deviceName.trim().isEmpty || deviceName.trim().length > 80) {
      throw ArgumentError("Enter a device name between 1 and 80 characters");
    }

    final decoded = jsonDecode(rawPayload);
    if (decoded is! Map ||
        decoded["type"] != "voiceassistant_mobile_pairing" ||
        decoded["version"] != 2) {
      throw const FormatException("This is not a VoiceAssistant pairing QR code");
    }
    final desktopIdentity = await inspectDesktopIdentity(rawPayload);
    final endpoint = _validatePairingEndpoint(decoded["endpoint"]);
    final sessionId = decoded["session_id"];
    final secret = decoded["secret"];
    final expiresRaw = decoded["expires_at"];
    if (sessionId is! String ||
        !RegExp(r"^[0-9a-f]{32}$").hasMatch(sessionId) ||
        secret is! String ||
        secret.length < 32 ||
        secret.length > 256 ||
        expiresRaw is! String) {
      throw const FormatException("QR pairing data is incomplete or invalid");
    }
    final expiresAt = DateTime.tryParse(expiresRaw);
    if (expiresAt == null ||
        !expiresAt.isAfter(DateTime.now()) ||
        expiresAt.isAfter(DateTime.now().add(const Duration(minutes: 6)))) {
      throw const FormatException("QR pairing code has expired or has an invalid lifetime");
    }

    final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
    isPairing = true;
    statusMessage = "Requesting desktop approval...";
    statusColor = "#F39C12";
    lastError = null;
    notifyListeners();
    try {
      final pinnedIdentityId = await _secureStorage.read(key: "desktop_identity_id");
      if (pinnedIdentityId == desktopIdentity["identity_id"]) {
        final pinnedPublicKey = await _secureStorage.read(key: "desktop_identity_public_key");
        final pinnedFingerprint = await _secureStorage.read(key: "desktop_identity_fingerprint");
        if (pinnedPublicKey != desktopIdentity["public_key"] ||
            pinnedFingerprint != desktopIdentity["fingerprint_sha256"]) {
          throw StateError(
            "This desktop identity changed unexpectedly. Pairing is blocked; verify recovery or re-pair explicitly.",
          );
        }
      }
      final requestUri = endpoint.replace(path: "/pairing/request");
      final resultUri = endpoint.replace(path: "/pairing/result");
      // Generate the device signing identity only after the user explicitly
      // scans a pairing QR. The private seed remains in platform secure storage.
      final identity = await MobileIdentityService().loadOrCreateForPairing();
      final normalizedName = deviceName.trim();
      final proofMessage = utf8.encode(
        "voiceassistant-mobile-pairing-v2\n"
        "$sessionId\n"
        "$normalizedName\n"
        "${identity.publicKey}\n"
        "$secret",
      );
      final proofSignature = base64Url
          .encode(await MobileIdentityService().sign(proofMessage))
          .replaceAll("=", "");
      await _postPairingJson(client, requestUri, {
        "session_id": sessionId,
        "secret": secret,
        "device_name": normalizedName,
        "public_key_ed25519": identity.publicKey,
        "proof_signature": proofSignature,
      });

      while (DateTime.now().isBefore(expiresAt)) {
        await Future.delayed(const Duration(seconds: 1));
        final result = await _postPairingJson(client, resultUri, {
          "session_id": sessionId,
          "secret": secret,
        });
        final state = result["status"];
        if (state == "denied") {
          throw StateError("The desktop user denied this device pairing");
        }
        if (state == "approved") {
          final credentials = result["credentials"];
          if (credentials is! Map ||
              credentials["device_id"] is! String ||
              credentials["device_token"] is! String) {
            throw const FormatException("Desktop returned invalid device credentials");
          }
          final socketEndpoint = endpoint.replace(scheme: "wss", path: "/ws");
          final deviceId = credentials["device_id"] as String;
          final deviceToken = credentials["device_token"] as String;

          // Persist the received credential before acknowledging it. If the
          // app is terminated immediately after the acknowledgement, the user
          // can still reconnect without having to enroll a ghost device again.
          await _secureStorage.write(key: "desktop_host", value: socketEndpoint.toString());
          await _secureStorage.write(key: "mobile_token", value: deviceToken);
          await _secureStorage.write(key: "mobile_device_id", value: deviceId);
          await _secureStorage.delete(key: "remote_session_id");
          await _secureStorage.delete(key: "remote_session_ticket");
          await _secureStorage.delete(key: "remote_session_expires_at_ms");
          await _secureStorage.write(key: "desktop_identity_id", value: desktopIdentity["identity_id"]!);
          await _secureStorage.write(key: "desktop_identity_public_key", value: desktopIdentity["public_key"]!);
          await _secureStorage.write(key: "desktop_identity_fingerprint", value: desktopIdentity["fingerprint_sha256"]!);
          await _secureStorage.write(key: "auto_connect", value: "false");
          hasSavedConnection = true;
          notifyListeners();

          // Acknowledge receipt before connecting. If this response is lost,
          // retrying the acknowledgement is safe and idempotent.
          Map<String, dynamic>? completion;
          for (var attempt = 0; attempt < 3; attempt++) {
            try {
              completion = await _postPairingJson(
                client,
                endpoint.replace(path: "/pairing/complete"),
                {"session_id": sessionId, "secret": secret},
              );
              break;
            } catch (_) {
              if (attempt == 2 || !DateTime.now().isBefore(expiresAt)) rethrow;
              await Future.delayed(const Duration(seconds: 1));
            }
          }
          if (completion?["status"] != "completed") {
            throw StateError("Desktop did not confirm credential receipt");
          }
          await _secureStorage.write(key: "auto_connect", value: "true");
          isPairing = false;
          notifyListeners();
          connect(
            socketEndpoint.toString(),
            deviceId: deviceId,
            token: deviceToken,
          );
          return;
        }
        if (state != "pending_approval" && state != "waiting") {
          throw StateError("Unexpected pairing status from desktop");
        }
        statusMessage = state == "pending_approval"
            ? "Waiting for approval on desktop..."
            : "Waiting for phone request...";
        notifyListeners();
      }
      throw StateError("Pairing code expired before desktop approval");
    } catch (error) {
      isPairing = false;
      statusMessage = "Pairing failed";
      statusColor = "#E74C3C";
      lastError = error.toString();
      notifyListeners();
      rethrow;
    } finally {
      client.close(force: true);
      if (isPairing) {
        isPairing = false;
        notifyListeners();
      }
    }
  }

  void connect(
    String host, {
    int port = 8765,
    String token = "",
    String deviceId = "",
    String remoteSessionId = "",
    String remoteSessionTicket = "",
    int remoteSessionExpiresAtMs = 0,
  }) {
    _manualDisconnect = false;
    _lastHost = host.trim();
    _lastToken = token.trim();
    _lastDeviceId = deviceId.trim();
    _lastRemoteSessionId = remoteSessionId.trim();
    _lastRemoteSessionTicket = remoteSessionTicket.trim();
    _lastRemoteSessionExpiresAtMs = remoteSessionExpiresAtMs;
    _lastPort = port;
    _reconnectAttempts = 0;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _connectInternal();
  }

  void _connectInternal() {
    final generation = ++_connectionGeneration;
    _connectTimeout?.cancel();
    _connectTimeout = null;
    try {
      _sub?.cancel();
    } catch (_) {}
    _sub = null;
    final previousChannel = _channel;
    _channel = null;
    try {
      previousChannel?.sink.close();
    } catch (_) {}

    final host = _lastHost?.trim() ?? "";
    if (host.isEmpty) {
      isConnecting = false;
      isConnected = false;
      statusMessage = "Enter desktop IP";
      statusColor = "#E74C3C";
      lastError = "Host empty";
      notifyListeners();
      return;
    }

    late final Uri endpoint;
    try {
      endpoint = Uri.parse(
        host.contains('://') ? host : "ws://$host:$_lastPort/ws",
      );
      if (!const {"ws", "wss"}.contains(endpoint.scheme) || endpoint.host.isEmpty) {
        throw const FormatException("Use a hostname/IP or a ws:// / wss:// URL");
      }
    } catch (error) {
      isConnecting = false;
      isConnected = false;
      statusMessage = "Invalid desktop address: $error";
      statusColor = "#E74C3C";
      lastError = statusMessage;
      notifyListeners();
      return;
    }

    final url = (endpoint.path.isEmpty || endpoint.path == "/")
        ? endpoint.replace(path: "/ws").toString()
        : endpoint.toString();
    final isLoopback = const {"localhost", "127.0.0.1", "::1"}.contains(endpoint.host);
    securityWarning = endpoint.scheme == "ws" && !isLoopback
        ? "Unencrypted WS connection. Use wss:// or a trusted VPN on LAN."
        : null;
    if (defaultTargetPlatform == TargetPlatform.iOS &&
        endpoint.scheme == "ws" &&
        !isLoopback) {
      isConnecting = false;
      isConnected = false;
      statusMessage = "iOS requires wss:// for remote desktop connections";
      statusColor = "#E74C3C";
      lastError = statusMessage;
      notifyListeners();
      return;
    }

    if (_lastRemoteSessionId.isNotEmpty &&
        (_lastRemoteSessionTicket.isEmpty ||
            _lastRemoteSessionExpiresAtMs <= DateTime.now().millisecondsSinceEpoch)) {
      unawaited(_clearRemoteSessionCredentials());
      _failConnect(
        "Remote session expired. Scan a fresh desktop QR code.",
        generation: generation,
        retry: false,
      );
      return;
    }

    isConnecting = true;
    isConnected = false;
    statusMessage = "Connecting...";
    statusColor = "#F39C12";
    lastError = null;
    notifyListeners();

    try {
      final channel = IOWebSocketChannel.connect(
        Uri.parse(url),
        headers: _lastRemoteSessionId.isNotEmpty &&
                _lastRemoteSessionTicket.isNotEmpty
            ? {
                "x-voiceassistant-device-id": _lastDeviceId,
                "x-voiceassistant-session-id": _lastRemoteSessionId,
                "x-voiceassistant-session-ticket": _lastRemoteSessionTicket,
              }
            : (_lastToken.isEmpty
                ? null
                : (_lastDeviceId.isNotEmpty
                    ? {
                        "x-voiceassistant-device-id": _lastDeviceId,
                        "x-voiceassistant-device-token": _lastToken,
                      }
                    : {"x-voiceassistant-token": _lastToken})),
        connectTimeout: const Duration(seconds: 5),
        pingInterval: const Duration(seconds: 20),
      );
      _channel = channel;

      _sub = channel.stream.listen(
        (data) {
          if (generation == _connectionGeneration) {
            _handleMessage(data);
          }
        },
        onError: (error) {
          if (generation != _connectionGeneration) return;
          debugPrint("WS error: $error");
          _failConnect(
            "Connection error (is desktop app running?)",
            generation: generation,
          );
        },
        onDone: () {
          if (generation != _connectionGeneration) return;
          final closeCode = channel.closeCode;
          final rejected = closeCode == 1008;
          if (rejected && _lastRemoteSessionId.isNotEmpty) {
            unawaited(_clearRemoteSessionCredentials());
          }
          _failConnect(
            rejected
                ? (_lastRemoteSessionId.isNotEmpty
                    ? "Remote session expired or was revoked. Scan a fresh desktop QR code."
                    : "Desktop rejected the mobile token. Check the saved token.")
                : "Connection lost",
            generation: generation,
            retry: !rejected,
          );
        },
        cancelOnError: true,
      );

      _connectTimeout = Timer(const Duration(seconds: 5), () {
        if (generation == _connectionGeneration && !isConnected && isConnecting) {
          _failConnect(
            "No response from desktop (check IP / app running on :8765)",
            generation: generation,
          );
        }
      });
    } catch (error) {
      _failConnect(
        "Failed to connect: $error",
        generation: generation,
      );
    }
  }

  void _failConnect(
    String message, {
    required int generation,
    bool retry = true,
  }) {
    if (generation != _connectionGeneration) return;
    ++_connectionGeneration;
    _connectTimeout?.cancel();
    _connectTimeout = null;
    try {
      _sub?.cancel();
    } catch (_) {}
    _sub = null;
    final oldChannel = _channel;
    _channel = null;
    try {
      oldChannel?.sink.close();
    } catch (_) {}
    isConnected = false;
    isConnecting = false;
    showSecurityGate = false;
    securityText = "";
    securityRequestId = null;
    approvalSubmitting = false;
    _approvalTimeout?.cancel();
    _approvalTimeout = null;
    statusMessage = message;
    statusColor = "#E74C3C";
    lastError = message;
    notifyListeners();
    if (retry) _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_manualDisconnect || _lastHost == null || _reconnectTimer != null) return;
    const delays = [1, 2, 4, 8, 16, 30];
    final delayIndex = math.min(_reconnectAttempts, delays.length - 1).toInt();
    final seconds = delays[delayIndex];
    _reconnectAttempts++;
    statusMessage = "Connection lost. Retrying in ${seconds}s...";
    statusColor = "#F39C12";
    notifyListeners();
    _reconnectTimer = Timer(Duration(seconds: seconds), () {
      _reconnectTimer = null;
      if (!_manualDisconnect && !isConnected && !isConnecting) {
        _connectInternal();
      }
    });
  }

  void disconnect() {
    _manualDisconnect = true;
    unawaited(_setAutoConnect(false));
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    ++_connectionGeneration;
    _connectTimeout?.cancel();
    _connectTimeout = null;
    try {
      _sub?.cancel();
    } catch (_) {}
    _sub = null;
    final oldChannel = _channel;
    _channel = null;
    try {
      oldChannel?.sink.close();
    } catch (_) {}
    isConnected = false;
    isConnecting = false;
    showSecurityGate = false;
    securityText = "";
    securityRequestId = null;
    approvalSubmitting = false;
    _approvalTimeout?.cancel();
    _approvalTimeout = null;
    statusMessage = "Disconnected";
    statusColor = "#E74C3C";
    notifyListeners();
  }

  void sendMessage(String text) {
    if (!isConnected || _channel == null) return;
    _channel!.sink.add(jsonEncode({
      "type": "user_message",
      "text": text,
    }));
  }

  void sendApproval(bool approved) {
    if (!isConnected ||
        _channel == null ||
        securityRequestId == null ||
        approvalSubmitting) {
      return;
    }
    try {
      _channel!.sink.add(jsonEncode({
        "type": "approval_response",
        "request_id": securityRequestId,
        "approved": approved,
      }));
      approvalSubmitting = true;
      lastError = null;
      _approvalTimeout?.cancel();
      _approvalTimeout = Timer(const Duration(seconds: 10), () {
        if (securityRequestId != null && approvalSubmitting) {
          approvalSubmitting = false;
          lastError = "Desktop did not acknowledge the approval. Retry or reconnect.";
          notifyListeners();
        }
      });
      notifyListeners();
    } catch (error) {
      approvalSubmitting = false;
      lastError = "Could not send approval. Check the connection and retry.";
      notifyListeners();
    }
  }

  void requestSessionState() {
    _sendRaw({"type": "get_session_state"});
  }

  void requestSchedules() {
    _sendRaw({"type": "list_schedules"});
  }

  void requestScheduleHistory() {
    _sendRaw({"type": "list_schedule_history"});
  }

  void requestUndo() {
    _sendRaw({"type": "undo_last"});
  }

  void cancelSchedule(String jobId) {
    _sendRaw({"type": "cancel_schedule", "job_id": jobId});
  }

  void _sendRaw(Map<String, dynamic> payload) {
    if (!isConnected || _channel == null) return;
    _channel!.sink.add(jsonEncode(payload));
  }

  void _applySessionState(Map<String, dynamic>? state) {
    if (state == null) return;
    sessionState = Map<String, dynamic>.from(state);
    undoAvailable = state["undo_available"] == true;
    jobsCount = (state["jobs_count"] is int)
        ? state["jobs_count"] as int
        : int.tryParse("${state["jobs_count"]}") ?? 0;
    lastUserText = state["last_user_text"]?.toString();
    lastStatus = state["last_status"]?.toString();
    final plan = state["active_plan"];
    activePlan = plan is Map ? Map<String, dynamic>.from(plan) : null;
    final rawJobs = state["jobs"];
    if (rawJobs is List) {
      jobs = rawJobs
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .toList();
      jobsCount = jobs.length;
    }
  }

  void _handleMessage(dynamic raw) {
    try {
      final data = jsonDecode(raw);
      if (data is! Map) return;
      final type = data["type"];

      switch (type) {
        case "connected":
          _connectTimeout?.cancel();
          _connectTimeout = null;
          isConnecting = false;
          isConnected = true;
          _reconnectAttempts = 0;
          statusMessage = data["message"] ?? "Connected to VoiceAssistant";
          statusColor = "#27AE60";
          lastError = null;
          unawaited(_persistConnection(_connectionGeneration));
          Future.delayed(const Duration(milliseconds: 200), () {
            if (isConnected) {
              requestSessionState();
              requestSchedules();
              requestScheduleHistory();
            }
          });
          break;

        case "status":
          statusMessage = data["message"] ?? statusMessage;
          statusColor = data["color"] ?? statusColor;
          break;

        case "transcript":
          messages.add({
            "role": data["role"] ?? "system",
            "text": data["text"] ?? "",
            "time": DateTime.now().toIso8601String(),
          });
          if (messages.length > 500) {
            messages.removeRange(0, messages.length - 500);
          }
          break;

        case "speak":
          if (onSpeak != null) {
            onSpeak!(data["text"] ?? "");
          }
          break;

        case "security_gate":
          _approvalTimeout?.cancel();
          approvalSubmitting = false;
          showSecurityGate = true;
          securityText = data["display_text"] ?? "";
          securityRisk = data["risk_level"] ?? "low";
          securityRequestId = data["request_id"]?.toString();
          break;

        case "approval_ack":
          if (data["request_id"]?.toString() == securityRequestId) {
            _approvalTimeout?.cancel();
            _approvalTimeout = null;
            approvalSubmitting = false;
            showSecurityGate = false;
            securityText = "";
            securityRequestId = null;
            if (data["accepted"] != true) {
              lastError = "Approval request expired or was rejected by the desktop session.";
            }
          }
          break;

        case "session_state":
          _applySessionState(
            data["state"] is Map
                ? Map<String, dynamic>.from(data["state"])
                : null,
          );
          break;

        case "schedules":
          final list = data["jobs"];
          if (list is List) {
            jobs = list
                .whereType<Map>()
                .map((e) => Map<String, dynamic>.from(e))
                .toList();
            jobsCount = jobs.length;
          }
          break;

        case "schedule_history":
          final records = data["records"];
          if (records is List) {
            scheduleHistory = records
                .whereType<Map>()
                .map((e) => Map<String, dynamic>.from(e))
                .toList();
          }
          break;

        case "undo_result":
          final result = data["result"];
          final ok = result is Map && result["ok"] == true;
          lastUndoMessage = ok
              ? (result["message"]?.toString() ?? "Undo completed")
              : (result is Map
                  ? (result["message"]?.toString() ?? "Undo failed")
                  : "Undo failed");
          if (!ok) lastError = lastUndoMessage;
          break;

        case "schedule_cancelled":
          lastStatus = data["message"]?.toString() ?? "Schedule updated";
          break;

        case "error":
          lastError = data["message"]?.toString();
          break;
      }

      notifyListeners();
    } catch (error) {
      debugPrint("Parse error: $error");
    }
  }

  void clearError() {
    if (lastError == null) return;
    lastError = null;
    notifyListeners();
  }

  void clearMessages() {
    messages.clear();
    notifyListeners();
  }

  @override
  void dispose() {
    _manualDisconnect = true;
    _reconnectTimer?.cancel();
    _connectTimeout?.cancel();
    _approvalTimeout?.cancel();
    _sub?.cancel();
    _channel?.sink.close();
    _reconnectTimer = null;
    _connectTimeout = null;
    _sub = null;
    _channel = null;
    super.dispose();
  }
}
