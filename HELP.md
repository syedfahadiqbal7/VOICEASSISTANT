# Agentic AI Architect & IT Admin Assistant - Help Guide

## Overview
Yeh ek advanced Voice + Text controlled AI Assistant hai jo Windows pe kaam karta hai.  
Yeh code likh sakta hai, PowerShell commands chala sakta hai, packages install kar sakta hai, aur aapke custom tools bhi use kar sakta hai.

---

## 1. Basic Controls

| Action | Kaise karein |
|--------|--------------|
| Start / Stop Listening | Bada button "Start Assistant" / "Stop Assistant" |
| Text se baat karna | Neeche text box mein type karke **Send** ya `Ctrl + Enter` |
| Speech band karna | **Stop Speaking** button ya `Esc` key |
| Hotkey se Start/Stop | `Ctrl + Shift + Space` |

---

## 2. Model Selection
Upar se aap alag-alag AI models choose kar sakte ho (qwen2.5-coder, llama3.1, etc.).  
Stronger model = better tool calling aur coding quality.

---

## 3. Dry-Run Mode
Jab **Dry-Run Mode** on hota hai:
- PowerShell commands **asli mein nahi chalti**
- Sirf bataya jata hai ke command kya karti
- Testing aur safety ke liye bohot useful

---

## 4. Security Approval Gate
Koi bhi tool chalane se pehle aapko **Approve** ya **Deny** karna padta hai.

- **Green** = Low risk
- **Orange** = Medium risk
- **Red** = High risk (delete, format, registry etc.)

---

## 5. Code Diff Preview
Jab AI koi file likhne wala hota hai, to pehle **Diff Viewer** khulta hai.  
Aap dekh sakte ho ke file mein kya change hone wala hai, phir Approve karo.

---

## 6. Conversation History
- Baat-cheet automatically save hoti rehti hai
- App band karke kholne pe bhi previous context yaad rehta hai
- **Clear History** se sab mita sakte ho

---

## 7. Export Conversation
**Export** button dabane se poori conversation Markdown file mein save ho jati hai (`exports/` folder mein).

---

## 8. Audit Log
Har approved/denied action ka record rakha jata hai.  
**Audit Log** button se dekh sakte ho.

---

## 9. Custom Tools Plugin System

### Naya Tool kaise banaye:

1. `templates/custom_tool_template.py` ko copy karo.
2. `plugins/` folder mein paste karo aur `BaseTool.name` ko unique rakho.
3. Class aur methods ko apne hisaab se change karo.
4. `tests/PLUGIN_AUTHORING.md` ke mutabiq sidecar manifest generate karo. Manifest mein exact tool name, SHA-256 checksum aur required permissions declare honi chahiye.
5. App restart karo. Invalid/missing manifest, checksum mismatch, undeclared tool ya duplicate tool name load nahi hoga.

### Example:
```python
class HelloWorldTool(BaseTool):
    @property
    def name(self): return "say_hello"
    ...

## 10. Secure mobile connection (updated)

- Desktop API default mein sirf `127.0.0.1` par listen karta hai.
- Phone se LAN connection ke liye `MOBILE_SECURITY_SETUP.md` follow karein.
- LAN mode mein `VOICEASSISTANT_MOBILE_HOST=0.0.0.0` aur kam az kam 32-character `VOICEASSISTANT_MOBILE_TOKEN` required hai.
- Flutter app mein desktop IP aur same token enter karein. Public internet par port 8765 expose na karein; VPN/TLS use karein.
- Mobile approval sirf us authenticated connection se accept hoti hai jise approval challenge bheji gayi ho.

## 11. Runtime dependencies

- Core desktop/API: `pip install -r requirements.txt`
- Windows integrations and optional document/media plugins: `pip install -r requirements-windows.txt`
- Tests: `pip install -r requirements-dev.txt`
- Windows par OCR ke liye `pytesseract` ke saath Tesseract OCR application bhi separately install honi chahiye.
