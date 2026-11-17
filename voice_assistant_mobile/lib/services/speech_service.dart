import 'package:speech_to_text/speech_to_text.dart' as stt;
import 'package:permission_handler/permission_handler.dart';
import 'package:flutter/foundation.dart';

class SpeechService {
  final stt.SpeechToText _speech = stt.SpeechToText();
  bool isListening = false;
  bool isAvailable = false;
  String lastWords = "";
  String? lastError;

  /// Request mic permission. Returns true only if granted.
  Future<bool> ensureMicPermission() async {
    lastError = null;
    var status = await Permission.microphone.status;

    if (status.isGranted) return true;

    if (status.isDenied) {
      status = await Permission.microphone.request();
      if (status.isGranted) return true;
    }

    if (status.isPermanentlyDenied || status.isRestricted) {
      lastError =
          "Microphone permanently denied. Open system Settings → Apps → Voice Assistant → Permissions → Microphone.";
      return false;
    }

    lastError = "Microphone permission not granted.";
    return false;
  }

  Future<bool> openSettings() async {
    return openAppSettings();
  }

  Future<bool> init() async {
    try {
      final granted = await ensureMicPermission();
      if (!granted) {
        isAvailable = false;
        return false;
      }

      isAvailable = await _speech.initialize(
        onStatus: (status) {
          debugPrint("Speech status: $status");
          if (status == "done" || status == "notListening") {
            isListening = false;
          }
        },
        onError: (error) {
          debugPrint("Speech error: $error");
          isListening = false;
          lastError = error.errorMsg;
        },
      );
      if (!isAvailable) {
        lastError =
            "Speech recognition not available on this device (or Google app / speech service missing).";
      }
      return isAvailable;
    } catch (e) {
      lastError = e.toString();
      isAvailable = false;
      return false;
    }
  }

  Future<void> startListening({
    required Function(String) onResult,
    Function()? onDone,
  }) async {
    if (!isAvailable) {
      final ok = await init();
      if (!ok) return;
    }

    isListening = true;
    lastWords = "";

    try {
      await _speech.listen(
        onResult: (result) {
          lastWords = result.recognizedWords;
          onResult(lastWords);

          if (result.finalResult) {
            isListening = false;
            if (onDone != null) onDone();
          }
        },
        listenFor: const Duration(seconds: 30),
        pauseFor: const Duration(seconds: 3),
        partialResults: true,
        cancelOnError: true,
        listenMode: stt.ListenMode.confirmation,
      );
    } catch (e) {
      isListening = false;
      lastError = e.toString();
    }
  }

  Future<void> stopListening() async {
    try {
      await _speech.stop();
    } catch (e) {
      lastError = e.toString();
    } finally {
      isListening = false;
    }
  }
}
