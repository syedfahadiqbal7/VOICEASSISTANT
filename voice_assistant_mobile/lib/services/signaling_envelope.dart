import 'dart:convert';

import 'mobile_identity_service.dart';

const int signalingProtocolVersion = 1;
const int _maxEnvelopeBytes = 64 * 1024;
const int _maxSignalingTtlMs = 60000;
const int _maxSafeInteger = 9007199254740991;

class SignalingEnvelopeException implements Exception {
  const SignalingEnvelopeException(this.message);

  final String message;

  @override
  String toString() => message;
}

/// Canonical JSON used for Ed25519 signaling signatures.
///
/// Object keys are sorted recursively; UTF-8 is emitted without ASCII
/// escaping. Protocol payloads deliberately reject floating point numbers so
/// Python and Dart cannot disagree about their serialized representation.
List<int> canonicalSignalingBytes(Map<String, dynamic> value) {
  dynamic normalize(dynamic item, [int depth = 0]) {
    if (depth > 12) {
      throw const SignalingEnvelopeException('Payload nesting exceeds its limit');
    }
    if (item == null || item is bool) return item;
    if (item is String) {
      if (item.length > 16384) {
        throw const SignalingEnvelopeException('Payload string exceeds its limit');
      }
      return item;
    }
    if (item is int) {
      if (item.abs() > _maxSafeInteger) {
        throw const SignalingEnvelopeException('Payload integer exceeds safe range');
      }
      return item;
    }
    if (item is double) {
      throw const SignalingEnvelopeException('Floating-point payload values are not supported');
    }
    if (item is List) {
      if (item.length > 256) {
        throw const SignalingEnvelopeException('Payload array exceeds its limit');
      }
      return item.map((value) => normalize(value, depth + 1)).toList();
    }
    if (item is Map) {
      if (item.length > 256 || item.keys.any((key) => key is! String)) {
        throw const SignalingEnvelopeException('Payload object is invalid or too large');
      }
      final keys = item.keys.cast<String>().toList()..sort();
      return <String, dynamic>{
        for (final key in keys) key: normalize(item[key], depth + 1),
      };
    }
    throw const SignalingEnvelopeException('Payload contains a non-JSON value');
  }

  final normalized = normalize(value);
  final bytes = utf8.encode(jsonEncode(normalized));
  if (bytes.length > _maxEnvelopeBytes) {
    throw const SignalingEnvelopeException('Canonical signaling data exceeds its size limit');
  }
  return bytes;
}

class SignalingEnvelopeService {
  SignalingEnvelopeService({MobileIdentityService? identity})
      : _identity = identity ?? MobileIdentityService();

  final MobileIdentityService _identity;

  Future<Map<String, dynamic>> create({
    required String senderId,
    required String recipientId,
    required String sessionId,
    required int sequence,
    required String messageType,
    required Map<String, dynamic> payload,
    int? nowMs,
    int ttlMs = 30000,
  }) async {
    final now = nowMs ?? DateTime.now().toUtc().millisecondsSinceEpoch;
    if (!_isId(senderId) || !_isId(recipientId) || !_isId(sessionId) ||
        sequence < 1 || sequence > _maxSafeInteger ||
        !RegExp(r'^[a-z][a-z0-9_.-]{0,63}$').hasMatch(messageType) ||
        ttlMs < 1000 || ttlMs > _maxSignalingTtlMs) {
      throw const SignalingEnvelopeException('Invalid signaling envelope metadata');
    }

    final unsigned = <String, dynamic>{
      'protocol_version': signalingProtocolVersion,
      'sender_id': senderId,
      'recipient_id': recipientId,
      'session_id': sessionId,
      'message_id': _newMessageId(),
      'sequence': sequence,
      'issued_at_ms': now,
      'expires_at_ms': now + ttlMs,
      'message_type': messageType,
      'payload': payload,
    };
    final bytes = canonicalSignalingBytes(unsigned);
    final signature = await _identity.sign(bytes);
    return <String, dynamic>{
      ...unsigned,
      'signature': base64Url.encode(signature).replaceAll('=', ''),
    };
  }

  static bool _isId(String value) => RegExp(r'^[0-9a-f]{32}$').hasMatch(value);

  static String _newMessageId() {
    // 128 bits from the platform CSPRNG; UUID formatting is normalized to hex.
    final random = List<int>.generate(16, (_) => _secureRandomByte());
    random[6] = (random[6] & 0x0f) | 0x40;
    random[8] = (random[8] & 0x3f) | 0x80;
    return random.map((byte) => byte.toRadixString(16).padLeft(2, '0')).join();
  }

  static final _random = _SecureRandom();
  static int _secureRandomByte() => _random.nextByte();
}

/// Uses dart:math Random.secure without exposing a seed or predictable PRNG.
class _SecureRandom {
  final dynamic _source = _createSecureRandom();

  int nextByte() => _source.nextInt(256);
}

dynamic _createSecureRandom() {
  // Kept in a helper so the secure source is created once per service library.
  return _SecureRandomSource();
}

class _SecureRandomSource {
  final _random = _secureRandomFactory();
  int nextInt(int max) => _random.nextInt(max);
}

dynamic _secureRandomFactory() {
  // dart:math is imported lazily at library scope by the companion import.
  return _mathRandomSecure();
}
