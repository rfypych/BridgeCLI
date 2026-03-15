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

**CRITICAL NOTE FOR AI:**
All smart pentest actions (`scan_nuclei`, `fuzz_dir`, `enum_subdomains`, `probe_alive`, `crawl_urls`) run in the background because they take a long time and we don't want you to timeout.
When you call them, they will immediately return a `pid`. You MUST use the `poll` action with that `pid` to check the status. Once `"done": true`, the `poll` action will return the cleaned up JSON array of results!

### `pentest_env` - Check Available Tools (Synchronous)
```json
{
  "action": "pentest_env"
}
```

### `enum_subdomains` - Subdomain Discovery (Background)
```json
{
  "action": "enum_subdomains",
  "domain": "example.com"
}
```
*Returns:* `{"pid": "123", "status": "running", ...}`

### `fuzz_dir` - Fuzzing (Background)
```json
{
  "action": "fuzz_dir",
  "target": "https://example.com/FUZZ",
  "wordlist": "/home/user/wordlists/custom_fuzz.txt"
}
```

### `probe_alive` - Live Host Check (Background)
```json
{
  "action": "probe_alive",
  "targets": ["sub1.example.com", "sub2.example.com"]
}
```

### `crawl_urls` - URL Crawling (Background)
```json
{
  "action": "crawl_urls",
  "target": "https://example.com",
  "depth": 3
}
```

### `scan_nuclei` - Vulnerability Scan (Background)
```json
{
  "action": "scan_nuclei",
  "target": "https://example.com",
  "severity": "critical,high"
}
```

### How to get the results:
Send a `poll` request until `"done": true`:
```json
{
  "action": "poll",
  "pid": "123"
}
```
*When done, this returns your parsed results (e.g. `{"done": true, "subdomains": [...]}`).*

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
