# 🤖 JWT Token Generator Bot

An advanced Telegram bot that processes account credentials (UID/password or access tokens) and generates JWT tokens via API. Includes VIP features like **multi-repo GitHub auto-upload** and **scheduled file processing**.

---

## ✨ Features

### 🆓 Free Features
- ✅ Process JSON files with UID/password OR access_token entries
- ✅ Mixed input formats supported in the same file
- ✅ Output in **exact input order** (order preservation)
- ✅ Region-wise token grouping (`accountsIndia.json`, etc.)
- ✅ Parallel processing (configurable concurrency)
- ✅ Automatic retry on failures (configurable rounds)
- ✅ Detailed error summaries

### 👑 VIP Features
- 🚀 Auto-upload tokens to **multiple GitHub repos**
- 🚀 Multiple GitHub configs per user (different repos or same repo with different files)
- 🚀 **Scheduled file processing** — daily at a specific time or at fixed intervals
- 🚀 Per-schedule GitHub repo binding
- 🚀 Auto-derived target filenames (`ind_ac.json` → `ind_token.json`)

---

## 📋 Commands

### Public Commands
| Command | Description |
|---|---|
| `/start` | Show welcome message and main menu |
| `/help` | Show full help with examples |
| `/vipstatus` | Check your VIP status |
| `/vipshop` | View VIP pricing plans |
| `/cancel` | Cancel current operation |

### VIP Commands
| Command | Description |
|---|---|
| `/setgithub <TOKEN> <owner/repo> <branch> <file.json>` | Add/update a GitHub config |
| `/removegithub <owner/repo> [file.json]` | Remove a specific GitHub config |
| `/mygithub` | List all your GitHub configs |
| `/setfile <Interval\|Time> <Name.json> [owner/repo] [Token.json]` | Schedule a file |
| `/removefile <Name.json> [owner/repo]` | Remove a schedule |
| `/scheduledfiles` | List all your active schedules |

### Admin Commands (Bot Owner Only)
| Command | Description |
|---|---|
| `/vip add <user_id> <days>` | Add/extend VIP |
| `/vip remove <user_id>` | Remove VIP + all configs + schedules |
| `/vip list` | List all VIPs |
| `/broadcast <message>` | Send message to all known users |

---

## 📁 Input File Formats

### Format 1: UID + Password
```json
[
  { "uid": "12345", "password": "PASS123" },
  { "uid": "67890", "password": "PASS456" }
]
