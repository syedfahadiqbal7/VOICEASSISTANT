import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:voice_assistant_mobile/services/mobile_identity_service.dart';

class DelayedIdentitySecretStorage extends FakeIdentitySecretStorage {
  @override
  Future<String?> read(String key) async {
    await Future<void>.delayed(Duration.zero);
    return super.read(key);
  }
}

class FakeIdentitySecretStorage implements MobileIdentitySecretStorage {
  final Map<String, String> values = {};

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> write(String key, String value) async {
    values[key] = value;
  }
}

List<int> _hex(String value) {
  return List<int>.generate(
    value.length ~/ 2,
    (index) => int.parse(value.substring(index * 2, index * 2 + 2), radix: 16),
  );
}

String _base64UrlNoPadding(List<int> bytes) =>
    base64Url.encode(bytes).replaceAll('=', '');

void main() {
  group('MobileIdentityService', () {
    test('load is read-only and does not generate an identity', () async {
      final storage = FakeIdentitySecretStorage();
      final service = MobileIdentityService(storage: storage);

      expect(await service.load(), isNull);
      expect(storage.values, isEmpty);
    });

    test('creates one device identity only when pairing explicitly requests it', () async {
      final storage = FakeIdentitySecretStorage();
      final service = MobileIdentityService(storage: storage);

      final created = await service.loadOrCreateForPairing();
      final storedRecord = storage.values.values.single;
      final loaded = await service.load();
      final repeated = await service.loadOrCreateForPairing();

      expect(created.publicKey, hasLength(43));
      expect(created.fingerprintSha256, hasLength(64));
      expect(storedRecord, contains('"algorithm":"Ed25519"'));
      expect(storedRecord, contains('"private_seed"'));
      expect(loaded?.publicKey, created.publicKey);
      expect(repeated.publicKey, created.publicKey);
      expect(repeated.fingerprintSha256, created.fingerprintSha256);
      expect(storage.values.length, 1);
    });

    test('concurrent pairing requests across service instances reuse one identity', () async {
      final storage = DelayedIdentitySecretStorage();
      final firstService = MobileIdentityService(storage: storage);
      final secondService = MobileIdentityService(storage: storage);

      final identities = await Future.wait([
        firstService.loadOrCreateForPairing(),
        secondService.loadOrCreateForPairing(),
      ]);

      expect(identities[0].publicKey, identities[1].publicKey);
      expect(identities[0].fingerprintSha256, identities[1].fingerprintSha256);
      expect(storage.values.length, 1);
      final loaded = await firstService.load();
      expect(loaded?.publicKey, identities[0].publicKey);
    });

    test('signatures verify and changed messages are rejected', () async {
      final service = MobileIdentityService(storage: FakeIdentitySecretStorage());
      final identity = await service.loadOrCreateForPairing();
      final message = utf8.encode('VoiceAssistant mobile identity v1');
      final signature = await service.sign(message);

      expect(signature, hasLength(64));
      expect(
        await MobileIdentityService.verify(
          publicKey: identity.publicKey,
          message: message,
          signature: signature,
        ),
        isTrue,
      );
      expect(
        await MobileIdentityService.verify(
          publicKey: identity.publicKey,
          message: [...message, 0x21],
          signature: signature,
        ),
        isFalse,
      );
    });

    test('malformed existing identity fails closed and is not overwritten', () async {
      final storage = FakeIdentitySecretStorage()
        ..values['voiceassistant.mobile_identity.v1'] = '{"schema_version":99}';
      final service = MobileIdentityService(storage: storage);
      final before = storage.values.values.single;

      await expectLater(
        service.loadOrCreateForPairing(),
        throwsA(isA<MobileIdentityException>()),
      );
      expect(storage.values.values.single, before);
    });

    test('public/private mismatch fails closed and does not regenerate', () async {
      final storage = FakeIdentitySecretStorage();
      final service = MobileIdentityService(storage: storage);
      await service.loadOrCreateForPairing();
      final record = jsonDecode(storage.values.values.single) as Map<String, dynamic>;
      record['public_key'] = _base64UrlNoPadding(List<int>.filled(32, 7));
      storage.values['voiceassistant.mobile_identity.v1'] = jsonEncode(record);
      final corrupted = storage.values.values.single;

      await expectLater(
        service.loadOrCreateForPairing(),
        throwsA(isA<MobileIdentityException>()),
      );
      expect(storage.values.values.single, corrupted);
    });

    test('matches the RFC 8032 Ed25519 empty-message test vector', () async {
      const publicHex =
          'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a';
      const signatureHex =
          'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155'
          '5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b';

      expect(
        await MobileIdentityService.verify(
          publicKey: _base64UrlNoPadding(_hex(publicHex)),
          message: const [],
          signature: _hex(signatureHex),
        ),
        isTrue,
      );
    });
  });
}
