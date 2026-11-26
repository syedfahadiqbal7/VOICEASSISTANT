import 'dart:convert';

import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:voice_assistant_mobile/services/mobile_identity_service.dart';
import 'package:voice_assistant_mobile/services/signaling_envelope.dart';

class _MemoryIdentityStorage implements MobileIdentitySecretStorage {
  final Map<String, String> values = {};

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> write(String key, String value) async {
    values[key] = value;
  }
}

String _b64(List<int> bytes) => base64Url.encode(bytes).replaceAll('=', '');


void main() {
  group('canonical signaling JSON', () {
    test('sorts nested object keys and emits UTF-8 deterministically', () {
      final bytes = canonicalSignalingBytes({
        'z': 1,
        'a': {'y': true, 'b': 'x'},
      });
      expect(
        utf8.decode(bytes),
        '{"a":{"b":"x","y":true},"z":1}',
      );
    });

    test('preserves non-ASCII text as UTF-8', () {
      final bytes = canonicalSignalingBytes({'text': 'سلام'});
      expect(utf8.decode(bytes), '{"text":"سلام"}');
    });

    test('rejects floating-point values', () {
      expect(
        () => canonicalSignalingBytes({'latency': 1.25}),
        throwsA(isA<SignalingEnvelopeException>()),
      );
    });

    test('rejects integers outside JavaScript safe range', () {
      expect(
        () => canonicalSignalingBytes({'sequence': 9007199254740992}),
        throwsA(isA<SignalingEnvelopeException>()),
      );
    });

    test('rejects excessive nesting', () {
      dynamic value = 0;
      for (var i = 0; i < 14; i++) {
        value = [value];
      }
      expect(
        () => canonicalSignalingBytes({'deep': value}),
        throwsA(isA<SignalingEnvelopeException>()),
      );
    });
  });

  test('creates an Ed25519 envelope verifiable by the shared identity verifier', () async {
    final storage = _MemoryIdentityStorage();
    const seedHex =
        '9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60';
    const publicHex =
        'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a';
    List<int> fromHex(String value) => [
      for (var i = 0; i < value.length; i += 2)
        int.parse(value.substring(i, i + 2), radix: 16),
    ];
    final publicBytes = fromHex(publicHex);
    final fingerprint = (await Sha256().hash(publicBytes)).bytes
        .map((byte) => byte.toRadixString(16).padLeft(2, '0'))
        .join();
    storage.values['voiceassistant.mobile_identity.v1'] = jsonEncode({
      'schema_version': 1,
      'algorithm': 'Ed25519',
      'private_seed': _b64(fromHex(seedHex)),
      'public_key': _b64(publicBytes),
      'fingerprint_sha256': fingerprint,
    });
    final identity = MobileIdentityService(storage: storage);
    final service = SignalingEnvelopeService(identity: identity);
    final envelope = await service.create(
      senderId: 'a' * 32,
      recipientId: 'b' * 32,
      sessionId: 'c' * 32,
      sequence: 1,
      messageType: 'sdp.offer',
      payload: {'sdp': 'v=0'},
      nowMs: 1700000000000,
    );
    final unsigned = Map<String, dynamic>.from(envelope)..remove('signature');
    final encodedSignature = envelope['signature'] as String;
    final signature = base64Url.decode(encodedSignature.padRight(
      encodedSignature.length + ((4 - encodedSignature.length % 4) % 4),
      '=',
    ));
    expect(
      await MobileIdentityService.verify(
        publicKey: _b64(publicBytes),
        message: canonicalSignalingBytes(unsigned),
        signature: signature,
      ),
      isTrue,
    );
  });

}
