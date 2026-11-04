; 覆盖安装时 exe 换成了新的图标资源，但 Explorer 按路径缓存图标，
; 快捷方式和「应用和功能」里还会画着上一版的样子。让外壳重建一次图标缓存。
!macro NSIS_HOOK_POSTINSTALL
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, i 0, i 0)'
!macroend
