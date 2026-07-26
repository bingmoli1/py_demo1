把快捷方式放在启动文件夹自启动

打包
```bash
 pyinstaller -F --distpath . v2.py
```
不显示cmd界面
```bash
  pyinstaller --onefile --noconsole --distpath . v2.py
```