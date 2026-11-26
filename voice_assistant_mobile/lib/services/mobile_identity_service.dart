import 'dart:convert';

import 'package:cryptography/cryptography.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

const String _identityStorageKey = 'voiceassistant.mobile_identity.v1';
const int _identitySchemaVersion = 1;
const int _maxSignedMessageBytes = 1024 * 1024;

class MobileIdentityException implements Exception {
  const MobileIdentityException(this.message);

  final String message;

  @override
  String toString() => message;
}

class MobileIdentityInfo {
  const MobileIdentityInfo({
    required this.publicKey,
    required this.fingerprintSha256,
  });

  final String publicKey;
  final String fingerprintSha256;
}

abstract interface class MobileIdentitySecretStorage {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
}

class FlutterMobileIdentitySecretStorage
    implements MobileIdentitySecretStorage {
  static const FlutterSecureStorage _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
    iOptions: IOSOptions(
      accessibility: KeychainAccessibility.first_unlock_this_device,
      synchronizable: false,
    ),
  );

  @override
  Future<String?> read(String key) => _storage.read(key: key);

  @override
  Future<void> write(String key, String value) =>
      _storage.write(key: key, value: value);
}

/// Device-bound Ed25519 key storage for future signed remote sessions.
///
/// A key is created only when the user explicitly starts QR pairing. The
/// private seed is kept in Android Keystore-backed encrypted storage or an
/// iOS ThisDeviceOnly Keychain item. Invalid existing records fail closed;
/// this class deliberately has no reset or silent-regeneration operation.
class MobileIdentityService {
  MobileIdentityService({MobileIdentitySecretStorage? storage})
      : _storage = storage ?? FlutterMobileIdentitySecretStorage();

  final MobileIdentitySecretStorage _storage;
  static final Ed25519 _algorithm = Ed25519();

  static String _encode(List<int> bytes) =>
      base64Url.encode(bytes).replaceAll('=', '');

  static List<int> _decode(String value, String field) {
    if (value.isEmpty || value.length > 256 || value.contains('=')) {
      throw MobileIdentityException('Mobile identity $field is malformed');
    }
    try {
      final decoded = base64Url.decode(
        value.padRight(value.length + ((4 - value.length % 4) % 4), '='),
      );
      if (_encode(decoded) != value) {
        throw const FormatException('Non-canonical base64url');
      }
      return decoded;
    } catch (_) {
      throw MobileIdentityException('Mobile identity $field is malformed');
    }
  }

  static Future<String> _fingerprint(List<int> publicKey) async {
    final digest = await Sha256().hash(publicKey);
    return digest.bytes
        .map((byte) => byte.toRadixString(16).padLeft(2, '0'))
        .join();
  }

  Future<Map<String, dynamic>?> _readRecord() async {
    final raw = await _storage.read(_identityStorageKey);
    if (raw == null) return null;
    if (raw.length > 4096) {
      throw const MobileIdentityException('Mobile identity record exceeds its size limit');
    }
    dynamic decoded;
    try {
      decoded = jsonDecode(raw);
    } catch (_) {
      throw const MobileIdentityException('Mobile identity record is malformed');
    }
    if (decoded is! Map<String, dynamic>) {
      throw const MobileIdentityException('Mobile identity record has an invalid shape');
    }
    const fields = {
      'schema_version',
      'algorithm',
      'private_seed',
      'public_key',
      'fingerprint_sha256',
    };
    if (decoded.keys.toSet().difference(fields).isNotEmpty ||
        decoded.keys.toSet().length != fields.length ||
        decoded['schema_version'] is! int ||
        decoded['schema_version'] != _identitySchemaVersion ||
        decoded['algorithm'] != 'Ed25519') {
      throw const MobileIdentityException('Mobile identity schema is unsupported');
    }
    final seedValue = decoded['private_seed'];
    final publicValue = decoded['public_key'];
    final fingerprint = decoded['fingerprint_sha256'];
    if (seedValue is! String ||
        publicValue is! String ||
        fingerprint is! String ||
        !RegExp(r'^[0-9a-f]{64}$').hasMatch(fingerprint)) {
      throw const MobileIdentityException('Mobile identity key metadata is invalid');
    }
    final seed = _decode(seedValue, 'private seed');
    final storedPublic = _decode(publicValue, 'public key');
    if (seed.length != 32 || storedPublic.length != 32) {
      throw const MobileIdentityException('Mobile identity key length is invalid');
    }
    final keyPair = await _algorithm.newKeyPairFromSeed(seed);
    try {
      final derivedPublic = (await keyPair.extractPublicKey()).bytes;
      final derivedFingerprint = await _fingerprint(derivedPublic);
      if (_encode(derivedPublic) != publicValue ||
          _encode(storedPublic) != publicValue ||
          derivedFingerprint != fingerprint) {
        throw const MobileIdentityException(
          'Mobile identity public/private key pair failed integrity validation',
        );
      }
    } finally {
      keyPair.destroy();
      seed.fillRange(0, seed.length, 0);
    }
    return decoded;
  }

  Future<MobileIdentityInfo?> load() async {
    final record = await _readRecord();
    if (record == null) return null;
    return MobileIdentityInfo(
      publicKey: record['public_key'] as String,
      fingerprintSha256: record['fingerprint_sha256'] as String,
    );
  }

  /// Create on first explicit pairing action; never overwrite an existing key.
  Future<MobileIdentityInfo> loadOrCreateForPairing() async {
    final existing = await _readRecord();
    if (existing != null) {
      return MobileIdentityInfo(
        publicKey: existing['public_key'] as String,
        fingerprintSha256: existing['fingerprint_sha256'] as String,
      );
    }

    final keyPair = await _algorithm.newKeyPair();
    try {
      final seed = await keyPair.extractPrivateKeyBytes();
      try {
        final publicKey = (await keyPair.extractPublicKey()).bytes;
        if (seed.length != 32 || publicKey.length != 32) {
          throw const MobileIdentityException('Generated Ed25519 key has an invalid length');
        }
        final record = <String, dynamic>{
          'schema_version': _identitySchemaVersion,
          'algorithm': 'Ed25519',
          'private_seed': _encode(seed),
          'public_key': _encode(publicKey),
          'fingerprint_sha256': await _fingerprint(publicKey),
        };
        // One secure-storage item keeps the identity record atomic at the API boundary.
        await _storage.write(_identityStorageKey, jsonEncode(record));
        final verified = await _readRecord();
        if (verified == null ||
            verified['public_key'] != record['public_key'] ||
            verified['fingerprint_sha256'] != record['fingerprint_sha256']) {
          throw const MobileIdentityException('Saved mobile identity could not be verified');
        }
        return MobileIdentityInfo(
          publicKey: record['public_key'] as String,
          fingerprintSha256: record['fingerprint_sha256'] as String,
        );
      } finally {
        seed.fillRange(0, seed.length, 0);
      }
    } finally {
      keyPair.destroy();
    }
  }

  Future<List<int>> sign(List<int> message) async {
    if (message.length > _maxSignedMessageBytes) {
      throw const MobileIdentityException('Signed message exceeds the 1 MiB limit');
    }
    final record = await _readRecord();
    if (record == null) {
      throw const MobileIdentityException('Mobile identity has not been initialized');
    }
    final seed = _decode(record['private_seed'] as String, 'private seed');
    final keyPair = await _algorithm.newKeyPairFromSeed(seed);
    try {
      final signature = await _algorithm.sign(message, keyPair: keyPair);
      return List<int>.unmodifiable(signature.bytes);
    } finally {
      keyPair.destroy();
      seed.fillRange(0, seed.length, 0);
    }
  }

  static Future<bool> verify({
    required String publicKey,
    required List<int> message,
    required List<int> signature,
  }) async {
    if (message.length > _maxSignedMessageBytes || signature.length != 64) {
      return false;
    }
    try {
      final publicBytes = _decode(publicKey, 'public key');
      if (publicBytes.length != 32) return false;
      final key = SimplePublicKey(publicBytes, type: KeyPairType.ed25519);
      return await _algorithm.verify(
        message,
        signature: Signature(signature, publicKey: key),
      );
    } catch (_) {
      return false;
    }
  }
}
