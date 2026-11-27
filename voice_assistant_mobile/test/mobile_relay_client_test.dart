import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:voice_assistant_mobile/services/mobile_relay_client.dart';

Map<String, dynamic> _payload({
  String relayUrl = 'wss://relay.example.com',
  int? issuedAt,
  int? expiresAt,
  String audience = 'voiceassistant-relay',
}) {
  final now = DateTime.now().millisecondsSinceEpoch;
  return {
    'type': 'voiceassistant_relay_session',
    'version': 1,
    'relay_url': relayUrl,
    'grant': {
      'protocol_version': 1,
      'audience': audience,
      'relay_url': relayUrl,
      'session_id': 'a' * 32,
      'desktop_id': 'b' * 32,
      'mobile_id': 'c' * 32,
      'desktop_public_key': 'D' * 43,
      'mobile_public_key': 'M' * 43,
      'issued_at_ms': issuedAt ?? now,
      'expires_at_ms': expiresAt ?? now + 300000,
    },
    'grant_signature': 'S' * 86,
    'desktop_identity': {
      'identity_id': 'b' * 32,
      'algorithm': 'Ed25519',
      'public_key': 'D' * 43,
      'fingerprint_sha256': '1' * 64,
    },
    'mobile_identity': {
      'device_id': 'c' * 32,
      'algorithm': 'Ed25519',
      'fingerprint_sha256': '2' * 64,
    },
  };
}

void main() {
  group('MobileRelayConnection QR validation', () {
    test('accepts a structurally valid, short-lived relay grant', () {
      final parsed = MobileRelayConnection.parsePayload(jsonEncode(_payload()));
      expect(parsed['type'], 'voiceassistant_relay_session');
      expect((parsed['grant'] as Map)['audience'], 'voiceassistant-relay');
    });

    test('rejects non-WSS and custom-path relay endpoints', () {
      for (final endpoint in [
        'https://relay.example.com',
        'ws://relay.example.com',
        'wss://relay.example.com/custom',
        'wss://user:password@relay.example.com',
        'wss://relay.example.com?token=secret',
        'wss://relay.example.com:9443',
      ]) {
        expect(
          () => MobileRelayConnection.parsePayload(
            jsonEncode(_payload(relayUrl: endpoint)),
          ),
          throwsA(isA<MobileRelayException>()),
          reason: endpoint,
        );
      }
    });

    test('rejects an incorrect relay audience', () {
      expect(
        () => MobileRelayConnection.parsePayload(
          jsonEncode(_payload(audience: 'another-service')),
        ),
        throwsA(isA<MobileRelayException>()),
      );
    });

    test('rejects expired and overlong grants', () {
      final now = DateTime.now().millisecondsSinceEpoch;
      expect(
        () => MobileRelayConnection.parsePayload(
          jsonEncode(_payload(issuedAt: now - 300000, expiresAt: now - 1)),
        ),
        throwsA(isA<MobileRelayException>()),
      );
      expect(
        () => MobileRelayConnection.parsePayload(
          jsonEncode(_payload(issuedAt: now, expiresAt: now + 900001)),
        ),
        throwsA(isA<MobileRelayException>()),
      );
    });

    test('rejects unexpected QR and grant fields', () {
      final extraTopLevel = _payload()..['unexpected'] = true;
      expect(
        () => MobileRelayConnection.parsePayload(jsonEncode(extraTopLevel)),
        throwsA(isA<MobileRelayException>()),
      );

      final extraGrant = _payload();
      (extraGrant['grant'] as Map)['unexpected'] = true;
      expect(
        () => MobileRelayConnection.parsePayload(jsonEncode(extraGrant)),
        throwsA(isA<MobileRelayException>()),
      );
    });

    test('rejects a malformed QR payload and signature encoding', () {
      expect(
        () => MobileRelayConnection.parsePayload('not-json'),
        throwsA(isA<MobileRelayException>()),
      );
      final payload = _payload()..['grant_signature'] = 'invalid';
      expect(
        () => MobileRelayConnection.parsePayload(jsonEncode(payload)),
        throwsA(isA<MobileRelayException>()),
      );
    });
  });
}
