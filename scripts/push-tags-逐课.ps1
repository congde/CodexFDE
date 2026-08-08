# 逐课推送 git tag（开课后按讲次/按周推送到 GitHub）
# 用法（在仓库根目录，PowerShell 运行）：
#   powershell -File scripts/push-tags-逐课.ps1 -Lesson L05        # 只推送第 05 讲
#   powershell -File scripts/push-tags-逐课.ps1 -Week 1            # 推送第一周全部课程
#   powershell -File scripts/push-tags-逐课.ps1 -All -DryRun       # 预览将推送的全部 tag
#   powershell -File scripts/push-tags-逐课.ps1 -All               # 推送全部 tag
param(
    [string]$Lesson,   # 单讲讲次前缀，如 L05
    [string]$Week,     # 周次，如 1 / 2 / 3 / 4
    [switch]$All,      # 推送全部 tag
    [switch]$DryRun    # 只显示将推送的 tag，不真正推送
)

$ErrorActionPreference = 'Stop'

# 定位 git（优先 PATH，其次 GitHub Desktop 自带 Git）
function Find-Git {
    $g = Get-Command git -ErrorAction SilentlyContinue
    if ($g) { return $g.Source }
    # GitHub Desktop 的 git 在版本化目录下，如 app-3.6.3\resources\app\git\cmd\git.exe
    $ghd = "$env:LOCALAPPDATA\GitHubDesktop"
    if (Test-Path $ghd) {
        $app = Get-ChildItem $ghd -Directory -Filter 'app-*' -ErrorAction SilentlyContinue |
               Sort-Object Name -Descending | Select-Object -First 1
        if ($app) {
            $c = Join-Path $app.FullName 'resources\app\git\cmd\git.exe'
            if (Test-Path $c) { return $c }
        }
    }
    $cands = @(
        'C:\Program Files\Git\cmd\git.exe',
        'C:\Program Files (x86)\Git\cmd\git.exe'
    )
    foreach ($c in $cands) { if (Test-Path $c) { return $c } }
    throw '未找到 git，请安装 Git 或把它加入 PATH。'
}
$git = Find-Git

# 周次 → 讲次前缀 映射
$weekMap = @{
    '1' = @('L00-开篇','L00-导读','L01','L02','L03','L04')
    '2' = @('L05','L06','L07','L08')
    '3' = @('L09','L10','L11','L12')
    '4' = @('L13','L14','L15','L16','L17')
}

# 收集要推送的 tag
$allTags = @(& $git tag)
$targets = [System.Collections.Generic.List[string]]::new()

if ($All) { $allTags.ForEach({ $targets.Add($_) }) }
elseif ($Week) {
    if (-not $weekMap.ContainsKey([string]$Week)) { throw "未知周次：$Week（应为 1-4）" }
    foreach ($p in $weekMap[[string]$Week]) {
        $allTags | Where-Object { $_ -like "$p*" } | ForEach-Object { $targets.Add($_) }
    }
}
elseif ($Lesson) {
    $match = $allTags | Where-Object { $_ -like "$Lesson*" }
    if (-not $match) { throw "未找到讲次前缀：$Lesson" }
    $match.ForEach({ $targets.Add($_) })
}
else {
    Write-Host '用法：-Lesson L05 | -Week 1 | -All'
    exit 2
}

if ($targets.Count -eq 0) { Write-Host '没有符合条件的 tag。'; exit 0 }

Write-Host "将推送 $($targets.Count) 个 tag："
$targets | ForEach-Object { Write-Host "  $_" }

if ($DryRun) { Write-Host 'DryRun：未实际推送。'; exit 0 }

# 逐课推送（每个单独一条命令，便于中途手动暂停/续推）
foreach ($t in $targets) {
    Write-Host "==> 推送 $t"
    & $git push origin "refs/tags/$t" 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { Write-Host "[暂停] $t 推送失败，可修复后重跑。"; exit 1 }
}
Write-Host '全部推送完成。'