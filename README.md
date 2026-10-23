# AppMesh — Desktop MCP

**Universal Desktop Application MCP Hub**: một MCP server chạy trên máy Windows của bạn, cho phép AI agent (Claude Desktop, Claude Code, Cursor, …) điều khiển phần mềm desktop thật — mở app, thao tác cửa sổ, vẽ trong **AutoCAD**, sửa project **CapCut** — thông qua các *tools* chuẩn Model Context Protocol.

> Repo chính thức: [TheOwlOps/AppMesh](https://github.com/TheOwlOps/AppMesh)

## Kiến trúc

```
Agent (MCP client) ──stdio / Streamable HTTP──> desktop-mcp server (Python, FastMCP)
                                                    ├─ system_*    OS, cửa sổ, chuột/phím (pywinauto / pyautogui)
                                                    ├─ autocad_*   COM Automation qua pyautocad (pywin32)
                                                    └─ capcut_*    sửa draft_content.json trực tiếp (không cần API)
```

Mỗi phần mềm = 1 *adapter* trong `src/desktop_mcp/adapters/`. Thêm phần mềm mới = thêm 1 file adapter + `register(mcp)`, không đụng core.

**Chiến thuật theo từng loại phần mềm:**
1. Có API/COM chính thức (AutoCAD, Office, Blender…) → adapter sâu, ổn định.
2. Không API nhưng file project mở được (CapCut `draft_content.json`) → sửa file, có backup trước khi ghi.
3. Chỉ có giao diện → UI Automation (đọc cây control, bấm theo tên) + screenshot/vision làm fallback.

## Danh sách tools (25)

| Nhóm | Tools |
|---|---|
| System (8) | `system_info`, `system_list_processes`, `system_launch_app`, `system_screenshot`, `system_list_windows`, `system_send_hotkey`, `system_type_text`, `system_ui_tree` |
| AutoCAD (11) | `autocad_status`, `autocad_open_drawing`, `autocad_new_drawing`, `autocad_list_entities`, `autocad_draw_line`, `autocad_draw_circle`, `autocad_add_text`, `autocad_list_layers`, `autocad_create_layer`, `autocad_save`, `autocad_run_command` |
| CapCut (6) | `capcut_list_projects`, `capcut_summary`, `capcut_get_texts`, `capcut_export_srt`, `capcut_replace_text`, `capcut_list_materials` |

## Cài đặt (Windows, PowerShell)

Yêu cầu: Python 3.11+.

```powershell
git clone git@github.com:TheOwlOps/AppMesh.git
cd AppMesh
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[windows]"
```

Extras `[windows]` cài thêm `pywin32`, `pyautocad`, `pywinauto`, `pyautogui`, `Pillow` — cần cho AutoCAD COM và các tool cửa sổ/chuột phím. Chỉ dùng CapCut adapter thì `pip install -e .` là đủ.

## Chạy server

```powershell
# stdio — để MCP client tự khởi động (cách dùng phổ biến):
.venv\Scripts\python -m desktop_mcp.server

# hoặc HTTP cục bộ (chỉ bind 127.0.0.1:8765, không lộ ra mạng):
.venv\Scripts\python -m desktop_mcp.server --http
```

## Kết nối agent

**Claude Desktop** — thêm vào `claude_desktop_config.json` (xem `examples/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "appmesh": {
      "command": "C:\\path\\to\\AppMesh\\.venv\\Scripts\\python.exe",
      "args": ["-m", "desktop_mcp.server"],
      "env": { "PYTHONPATH": "C:\\path\\to\\AppMesh\\src" }
    }
  }
}
```

**Cursor** dùng cùng định dạng trong `.cursor/mcp.json` (xem `examples/cursor_mcp.json`).

## An toàn

Server này điều khiển máy thật, nên mặc định:

- Chỉ bind `127.0.0.1` khi chạy HTTP; stdio chỉ nói chuyện với client đã khởi động nó.
- Mọi đường dẫn ghi file phải nằm dưới thư mục nhà của bạn (hoặc khai báo thêm qua biến môi trường `DESKTOP_MCP_ALLOWED_PATHS`, phân tách bằng `;` trên Windows).
- Mọi thao tác ghi (mở app, sửa draft CapCut, chạy lệnh AutoCAD…) đều ghi vào audit log `~/.desktop-mcp/audit.jsonl`.
- CapCut: **đóng CapCut trước khi sửa draft**; adapter luôn tạo bản backup `draft_content.json.bak-<timestamp>` trước mỗi lần ghi.
- `autocad_run_command` chạy lệnh AutoCAD tùy ý (tương đương gõ lệnh trong AutoCAD) — mạnh và nguy hiểm, chỉ để agent dùng khi bạn thực sự muốn.

## Biến môi trường

| Biến | Ý nghĩa |
|---|---|
| `CAPCUT_PROJECTS_DIR` | Ghi đè thư mục chứa các project CapCut (mặc định: `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft`) |
| `DESKTOP_MCP_ALLOWED_PATHS` | Thêm thư mục được phép truy cập |
| `DESKTOP_MCP_HOME` | Thư mục chứa audit log (mặc định `~/.desktop-mcp`) |

## Giới hạn hiện tại (MVP)

- AutoCAD adapter cần AutoCAD đã cài + bản quyền trên máy; chưa test trên máy Windows thật trong môi trường phát triển này (logic đã test bằng AutoCAD giả lập — 46 test tự động đều pass).
- CapCut không có API chính thức: adapter làm việc trên format `draft_content.json` hiện tại; ByteDance đổi format thì adapter phải cập nhật theo. Export video vẫn phải qua giao diện CapCut (tool UI automation hỗ trợ thao tác cơ bản).
- Các tool cửa sổ/chuột phím chỉ chạy trên Windows.

## Phát triển

```powershell
pip install -e ".[dev]"
python -m pytest -q
```

Thêm adapter mới: tạo `src/desktop_mcp/adapters/<app>.py` với class adapter (logic thuần, test được không cần app thật) + hàm `register(mcp)`, rồi đăng ký trong `server.py`.
