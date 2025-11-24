## Observatory User Presence Monitoring System (Windows setup with RDP, Flask + PowerShell client)

This README explains how to set up and test the Flask server and the Windows PowerShell auto‑start client.

---
## Prerequisites
- Windows 11 machine (observatory PC)
- Python 3 installed (`https://www.python.org/downloads/`)
- pip available (ships with Python)
- Administrator rights (for Task Scheduler)

---
## 1) Install and start the Flask server
1. Open PowerShell or Command Prompt.
2. Install Flask:
   ```powershell
   pip install flask
   ```
3. Create a directory, e.g. `C:\observatory_presence`.
4. Copy `observatory_presence.py` into that directory.
5. Start the server for testing:
   ```powershell
   cd C:\observatory_presence
   python observatory_presence.py
   ```
6. Open a browser → `http://localhost:5000`  
   → You should see a small web UI ("Observatory Status").

---
## 2) Prepare the PowerShell client
1. Place `autostart_client_prompt.ps1` in a directory, e.g. `C:\observatory_presence`.
2. The script asks for user name and an optional note, then sends periodic heartbeats.

---
## 3) Configure Task Scheduler
1. Open "Task Scheduler".
2. Create a new task:

   - Name: "Observatory Presence Client"
   - Trigger: "At log on" (current user or all users)
   - Action: "Start a program"

     - Program/script: `powershell.exe`

     - Arguments: `-ExecutionPolicy Bypass -File "C:\observatory_presence\autostart_client_prompt.ps1"`
   - Conditions: enable "Run only when user is logged on".
3. Save.

---
## 4) Test run
1. Start the Flask server manually (`python observatory_presence.py`).
2. Log in to the Windows machine via RDP.

   → At login a PowerShell prompt should appear:

   - "Please enter your name (observer)"

   - "Optional: Target / note"

3. After entering the data, open `http://localhost:5000` in the browser.

   → Your name and target should now be visible.

4. Wait ~2 minutes and end the RDP session.

   → The server will auto‑release the session (timeout due to missing heartbeats).

---
## 5) Useful notes
- For continuous operation: also start the Flask server via Task Scheduler or as a Windows service.

- Security: If you expose the web UI on the network, add TLS and authentication.

- Extensions: Integration with NINA or Maxim DL is possible (e.g., automatic status updates when a capture sequence starts).


---
## Quick test (without Task Scheduler)
- Terminal 1: start the Flask server (`python observatory_presence.py`)

- Terminal 2: open PowerShell and run the script manually:

  ```powershell
  cd C:\observatory_presence
  powershell -ExecutionPolicy Bypass -File autostart_client_prompt.ps1
  ```

- Open a browser → `http://localhost:5000` and check the status.
