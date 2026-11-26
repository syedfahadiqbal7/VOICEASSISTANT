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
}
