把快捷方式放在启动文件夹自启动

## 打包 exe

依赖（playwright / psutil / PyInstaller）装在 Python 3.14.7，用绝对路径调用，避免 `python` 指向没装依赖的环境：

```
C:\Users\12416\AppData\Local\Programs\Python\Python314\python.exe
```

**带控制台**（当前 `v2.exe` 就是这种，能看到运行日志，排查问题时用）：

```bash
"C:\Users\12416\AppData\Local\Programs\Python\Python314\python.exe" -m PyInstaller -F --distpath . v2.py
```

**不显示 cmd 界面**（放进启动文件夹、不想弹黑框时）：

```bash
"C:\Users\12416\AppData\Local\Programs\Python\Python314\python.exe" -m PyInstaller --onefile --noconsole --distpath . v2.py
```

若已把 PyInstaller 装进当前 PATH，可简写成 `pyinstaller -F --distpath . v2.py`。

说明：

- `-F` / `--onefile` 打成单个 exe，`--distpath .` 输出到项目根目录并覆盖旧的 `v2.exe`。
- **改完 `v2.py` 必须重新打包**，否则双击 exe 跑的还是打包那一刻的代码。
- 打包前确认 `v2.exe` 没在运行（自启动可能占着文件），否则覆盖失败。
- 改了代码但 exe 时间戳/内容没更新（命中了增量缓存）时，加 `--clean` 强制重建：
  ```bash
  "C:\Users\12416\AppData\Local\Programs\Python\Python314\python.exe" -m PyInstaller --clean -F --distpath . v2.py
  ```
- 构建会生成 `v2.spec` 和 `build/`，可删，不影响 exe 运行。
