# 🌉 Remote Bridge System v4.5 (Pentest Edition)

A lightweight, stable system for remote command execution over HTTP. Perfect for remote development, debugging, file transfer, and **autonomous AI pentesting**.

## 🛡️ Pentest Edition Features

This version is specifically enhanced for Autonomous AI Agents (like z.ai) to perform Bug Bounty and Penetration Testing via a Cloudflare Tunnel:
- **Smart Actions**: `scan_nuclei`, `enum_subdomains`, `fuzz_dir` output clean, structured JSON to save AI context tokens.
- **Background Processes**: Never hit cloud AI timeouts on long scans (`nmap`, `ffuf`) using the `bg` and `poll` mechanics.
- **Environment Automation**: Included `setup_pentest_env.sh` script to automatically download Go, Nuclei, Subfinder, Ffuf, Nmap, and SecLists.
- **File Transfer**: Chunked upload/download with progress bars.
- **Authentication**: Optional API key protection.

---

## 🚀 Quick Start for Bug Hunters

### Step 1: Install Offensive Tools
Run the setup script on your local machine (Kali/WSL) to install the necessary ProjectDiscovery tools and wordlists:
```bash
sudo ./setup_pentest_env.sh
```

### Step 2: Start the Agent & Tunnel
```bash
# Easiest way (starts agent + cloudflared tunnel automatically)
python3 launch.py

# With authentication
python3 launch.py --api-key your-secret-key
```

Output:
```
  TUNNEL READY
  URL:  https://xxxx-xxxx-xxxx-xxxx.trycloudflare.com
```

### Step 3: Give to AI
Copy the contents of `AI_SYSTEM_PROMPT.md` and paste it into your AI Agent's system instructions. Give it the Cloudflare tunnel URL and let it hunt!

---

## 🤖 Smart AI Pentest Endpoints

The Bridge Agent accepts HTTP `POST` requests at the root path (`/`). Send JSON payloads to execute commands.

### `pentest_env` - Check Available Tools
```json
{
  "action": "pentest_env"
}
```

### `enum_subdomains` - Subdomain Discovery (Subfinder)
```json
{
  "action": "enum_subdomains",
  "domain": "example.com"
}
```

### `fuzz_dir` - Fuzzing (Ffuf)
```json
{
  "action": "fuzz_dir",
  "target": "https://example.com/FUZZ",
  "wordlist": "/home/user/wordlists/custom_fuzz.txt"
}
```

### `scan_nuclei` - Vulnerability Scan
```json
{
  "action": "scan_nuclei",
  "target": "https://example.com",
  "severity": "critical,high"
}
```

---

## 📖 Standard Command Reference

### Synchronous Execution
```json
{
  "action": "exec",
  "command": "ls -la",
  "timeout": 60
}
```

### Background Execution (for long tasks like Nmap)
```json
{
  "action": "bg",
  "command": "nmap -p- -T4 target.com > scan.txt"
}
```
*(Returns a `pid`)*

```json
{
  "action": "poll",
  "pid": "1234"
}
```

---

## 🔐 Security

### Enable Authentication

**Agent side:**
```bash
python3 launch.py --api-key my-secret-key-123
```

**Controller side / AI Agent:**
Include the header: `Authorization: Bearer my-secret-key-123`

### Security Tips
1. **Never share your tunnel URL publicly** while the agent is running.
2. **Use strong API keys**.
3. **Monitor logs** - Check agent console for executed commands.

## 📄 License
MIT License
