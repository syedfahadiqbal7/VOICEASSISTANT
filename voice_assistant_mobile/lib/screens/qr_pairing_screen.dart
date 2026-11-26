import 'dart:async';

import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:provider/provider.dart';

import '../services/websocket_service.dart';

class QrPairingScreen extends StatefulWidget {
  const QrPairingScreen({super.key});

  @override
  State<QrPairingScreen> createState() => _QrPairingScreenState();
}

class _QrPairingScreenState extends State<QrPairingScreen> {
  final TextEditingController _nameController =
      TextEditingController(text: "My phone");
  final MobileScannerController _scannerController = MobileScannerController(
    detectionSpeed: DetectionSpeed.noDuplicates,
    formats: const [BarcodeFormat.qrCode],
  );

  bool _scanHandled = false;
  bool _pairing = false;
  String? _error;

  Future<void> _onDetect(BarcodeCapture capture) async {
    if (_scanHandled || _pairing) return;
    String? raw;
    for (final barcode in capture.barcodes) {
      final value = barcode.rawValue;
      if (value != null && value.trim().isNotEmpty) {
        raw = value.trim();
        break;
      }
    }
    if (raw == null) return;

    setState(() {
      _scanHandled = true;
      _pairing = true;
      _error = null;
    });
    unawaited(_scannerController.stop());

    try {
      final decoded = jsonDecode(raw);
      final isRemoteSession = decoded is Map &&
          decoded["type"] == "voiceassistant_remote_session" &&
          decoded["version"] == 1;
      final desktopIdentity =
          await WebSocketService.inspectDesktopIdentity(raw);
      final fingerprint = desktopIdentity["fingerprint_sha256"]!;
      final groupedFingerprint = List.generate(
        fingerprint.length ~/ 4,
        (index) => fingerprint.substring(index * 4, index * 4 + 4),
      ).join(" ");
      final trusted = await showDialog<bool>(
        context: context,
        barrierDismissible: false,
        builder: (dialogContext) => AlertDialog(
          title: const Text("Verify this desktop"),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                "Compare this fingerprint with the one displayed in the "
                "VoiceAssistant desktop pairing window. Continue only if "
                "every group matches exactly.",
              ),
              const SizedBox(height: 16),
              SelectableText(
                groupedFingerprint,
                style: const TextStyle(
                  fontFamily: "monospace",
                  fontWeight: FontWeight.bold,
                  letterSpacing: 0.8,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                "Identity: ${desktopIdentity["identity_id"]}",
                style: Theme.of(dialogContext).textTheme.bodySmall,
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: const Text("Cancel"),
            ),
            FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: const Text("Fingerprint matches"),
            ),
          ],
        ),
      );
      if (!mounted) return;
      if (trusted != true) {
        setState(() {
          _pairing = false;
          _scanHandled = false;
        });
        await _scannerController.start();
        return;
      }
      final service = context.read<WebSocketService>();
      if (isRemoteSession) {
        await service.connectRemoteFromQr(raw);
      } else {
        await service.pairFromQr(
          raw,
          deviceName: _nameController.text.trim(),
        );
      }
      if (mounted) Navigator.of(context).pop(true);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _pairing = false;
        _scanHandled = false;
        _error = error.toString();
      });
      try {
        await _scannerController.start();
      } catch (_) {}
    }
  }

  @override
  Widget build(BuildContext context) {
    final ws = context.watch<WebSocketService>();
    return Scaffold(
      backgroundColor: const Color(0xFF0F172A),
      appBar: AppBar(
        title: const Text("Connect to desktop"),
        backgroundColor: const Color(0xFF1E293B),
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            const Text(
              "Scan a new-pairing or remote-session QR code shown in the VoiceAssistant desktop app.",
              style: TextStyle(color: Colors.white, fontSize: 16),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _nameController,
              enabled: !_pairing,
              maxLength: 80,
              style: const TextStyle(color: Colors.white),
              decoration: InputDecoration(
                labelText: "Name for this device",
                labelStyle: const TextStyle(color: Colors.white70),
                filled: true,
                fillColor: const Color(0xFF1E293B),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
              ),
            ),
            const SizedBox(height: 12),
            ClipRRect(
              borderRadius: BorderRadius.circular(16),
              child: SizedBox(
                height: 320,
                child: _pairing
                    ? Container(
                        color: const Color(0xFF1E293B),
                        alignment: Alignment.center,
                        padding: const EdgeInsets.all(24),
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            const CircularProgressIndicator(),
                            const SizedBox(height: 20),
                            Text(
                              ws.statusMessage,
                              textAlign: TextAlign.center,
                              style: const TextStyle(color: Colors.white),
                            ),
                            const SizedBox(height: 8),
                            const Text(
                              "Complete the requested approval or remote-session verification.",
                              textAlign: TextAlign.center,
                              style: TextStyle(color: Colors.white70),
                            ),
                          ],
                        ),
                      )
                    : MobileScanner(
                        controller: _scannerController,
                        onDetect: _onDetect,
                      ),
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(
                _error!,
                style: const TextStyle(color: Colors.redAccent),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              FilledButton.icon(
                onPressed: () {
                  setState(() => _error = null);
                  unawaited(_scannerController.start());
                },
                icon: const Icon(Icons.qr_code_scanner),
                label: const Text("Scan again"),
              ),
            ],
            const SizedBox(height: 16),
            const Text(
              "Only scan a code displayed by a desktop you trust. QR grants are signed by the desktop identity and exchanged over HTTPS. New devices still require desktop approval.",
              style: TextStyle(color: Colors.white70, fontSize: 12),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }

  @override
  void dispose() {
    _nameController.dispose();
    unawaited(_scannerController.dispose());
    super.dispose();
  }
}
