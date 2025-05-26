# Custom_WinProcess
# 主要用途：通过自定义一个空的进程，在任务管理器中运行，占用极少的资源，从而跳过一些终端软件对进程的检查。

# PS:目前这个版本只在 Mac M1 中部署的 Windows 11 ARM 进行了测试， 并且火绒没有提示。
我尝试增加对创建的进程的 资源监控 ，但是在mac 平台中会报错，暂时移除了。
后续，希望有需要的，有能力的大佬，可以帮忙优化，满足全平台的需求。

可以直接现在我已经打包好的 支持在Windows 11 ARM 平台中运行的 程序。


# 补充：
Windows 打包命令

pyinstaller --name "动态进程管理器（可以自定义名称）" ^
            --onefile ^
            --windowed ^
            --icon="icon.ico" ^
            --add-data "icon.ico:." ^
            --add-binary "_template_dummy.exe:." ^
            custom_process_name.py
