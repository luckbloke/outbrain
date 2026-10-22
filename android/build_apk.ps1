<#
外脑 · Cordis Brain —— 在 Windows 上调用 kivy/buildozer 容器构建 Android APK

用法：
    powershell -ExecutionPolicy Bypass -File android\build_apk.ps1

可选参数：
    -Image    容器镜像（默认 kivy/buildozer:latest）
    -NoStart  不自动尝试启动 Docker Desktop

原理：
    1. 确认 Docker 引擎可用（不可用则尝试启动 Docker Desktop 并等待）；
    2. 在容器里运行 android/build_android.sh（bypass 镜像的 entrypoint，直接用 bash）；
    3. 容器内用 buildozer + python-for-android 打出 debug APK，产物回传到 android/dist/。

说明：
    · 命名卷 cordis_buildozer_home 挂到 /root/.buildozer，用于持久化
      Android SDK / NDK / gradle 缓存 / p4a 源码 / 已编译的 python 发行版。
      第一次构建要下载约 2~3 GB，之后重跑快很多。
    · 构建用 root：容器只是构建沙箱，产物在 android/dist/。
    · 本机直连 github.com 不通，build_android.sh 会改从 codeload.github.com 取 p4a 源码。
#>
param(
    [string]$Image = 'kivy/buildozer:latest',
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path   # ...\android
$src = Split-Path -Parent $here                           # 项目根
Write-Host "项目根目录: $src"

# ---------- 1) 确保 Docker 引擎在跑 ----------
function Test-Docker {
    try { docker info *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}

if (-not (Test-Docker)) {
    if ($NoStart) { throw "Docker 引擎未运行（已指定 -NoStart）。请先启动 Docker Desktop。" }
    Write-Host "Docker 引擎未运行，尝试启动 Docker Desktop ..."
    $candidates = @(
        'D:\Docker Desktop\Docker\Docker Desktop.exe',
        (Join-Path ${env:ProgramFiles} 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path ${env:LOCALAPPDATA} 'Docker\Docker Desktop.exe')
    ) | Where-Object { Test-Path $_ }

    if ($candidates) { Start-Process $candidates[0] } else { Write-Warning "未找到 Docker Desktop.exe" }

    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 5
        if (Test-Docker) { $ok = $true; break }
        Write-Host ("  等待 Docker 引擎就绪 ... ({0}s)" -f (($i + 1) * 5))
    }
    if (-not $ok) { throw "Docker 引擎仍不可用，请手动启动 Docker Desktop 后重试。" }
}
Write-Host "Docker 引擎: 就绪"

# ---------- 2) 拉镜像 ----------
docker image inspect $Image *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "拉取镜像 $Image ..."
    docker pull $Image
    if ($LASTEXITCODE -ne 0) { throw "拉取镜像失败：$Image" }
}

# ---------- 3) 构建 ----------
$volume = 'cordis_buildozer_home'
Write-Host "开始构建（首次约 30~60 分钟）..."
$dockerArgs = @(
    'run', '--rm',
    '--user', 'root',
    '-e', 'HOME=/root',
    '-e', 'GRADLE_USER_HOME=/root/.buildozer/.gradle',
    '-e', 'SRC=/src',
    '-e', 'WORK=/root/.buildozer/work',
    '-v', "${src}:/src",
    '-v', "${volume}:/root/.buildozer",
    '--entrypoint', 'bash',
    $Image,
    '/src/android/build_android.sh'
)
& docker @dockerArgs
$code = $LASTEXITCODE

if ($code -ne 0) {
    throw "构建失败（docker 退出码 $code）。完整日志在容器内 /root/.buildozer/work/buildozer.log。"
}

Write-Host ""
Write-Host "完成！APK 在 android\dist\ ："
Get-ChildItem (Join-Path $here 'dist') -Filter *.apk -ErrorAction SilentlyContinue |
    Format-Table Name, @{N = 'MB'; E = { [math]::Round($_.Length / 1MB, 1) } }, LastWriteTime -AutoSize
