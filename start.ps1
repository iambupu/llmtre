[CmdletBinding()]
param(
    [ValidateSet("dev", "dist")]
    [string]$Mode = "dev",
    [switch]$NoBrowser,
    [switch]$SkipInstall,
    [switch]$CheckOnly,
    [int]$SmokeSeconds = 0
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendDir = Join-Path $ProjectRoot "frontend"
$LogDir = Join-Path $ProjectRoot "logs\start"
$BackendPort = 5000
$FrontendPort = 5173
$BackendUrl = "http://localhost:$BackendPort"
$FrontendDevUrl = "http://localhost:$FrontendPort/app/"
$FlaskAppUrl = "$BackendUrl/app"
$StartedProcesses = New-Object System.Collections.Generic.List[System.Diagnostics.Process]

Set-Location $ProjectRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Write-Step {
    <#
    功能：输出一键启动过程中的阶段信息，方便用户判断当前卡在哪一步。
    入参：Message（string）：需要显示的中文阶段说明。
    出参：无；直接写入当前 PowerShell 控制台。
    异常：Write-Host 本身失败时由 PowerShell 抛出异常，不做额外捕获。
    #>
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[llmtre] $Message" -ForegroundColor Cyan
}

function Write-Warn {
    <#
    功能：输出非致命警告，通常用于端口已占用、依赖缺失或降级路径说明。
    入参：Message（string）：需要显示的中文警告说明。
    出参：无；直接写入当前 PowerShell 控制台。
    异常：Write-Host 本身失败时由 PowerShell 抛出异常，不做额外捕获。
    #>
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[llmtre] 警告：$Message" -ForegroundColor Yellow
}

function Resolve-RequiredCommand {
    <#
    功能：解析必须存在的外部命令，失败时给出明确错误。
    入参：Names（string[]）：候选命令名，按优先级排列。
    出参：string：第一个可执行命令的完整路径或命令名。
    异常：所有候选命令都不可用时抛出 InvalidOperationException。
    #>
    param([Parameter(Mandatory = $true)][string[]]$Names)
    foreach ($name in $Names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            return $command.Source
        }
    }
    throw "找不到必需命令：$($Names -join ', ')"
}

function Resolve-PythonExecutable {
    <#
    功能：选择后端启动使用的 Python；优先使用仓库内 Windows 虚拟环境。
    入参：无。
    出参：string：Python 可执行文件路径。
    异常：仓库虚拟环境和 PATH 中的 python 都不可用时抛出异常。
    #>
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }
    Write-Warn "未找到 .venv\Scripts\python.exe，将使用 PATH 中的 python。"
    return Resolve-RequiredCommand -Names @("python.exe", "python")
}

function Resolve-NpmExecutable {
    <#
    功能：选择前端启动和构建使用的 npm 命令。
    入参：无。
    出参：string：npm 可执行文件路径。
    异常：PATH 中不存在 npm.cmd 或 npm 时抛出异常。
    #>
    return Resolve-RequiredCommand -Names @("npm.cmd", "npm")
}

function Test-TcpPortOpen {
    <#
    功能：检查本机 TCP 端口是否已经可连接，用于判断服务是否已启动或端口被占用。
    入参：Port（int）：要检查的本机端口号。
    出参：bool：端口可连接返回 true，否则返回 false。
    异常：连接检查中的网络异常会被内部捕获并视为端口未开放。
    #>
    param([Parameter(Mandatory = $true)][int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(300, $false)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Wait-TcpPort {
    <#
    功能：等待指定端口进入可连接状态，作为服务启动成功的轻量验收。
    入参：Port（int）：目标端口；Name（string）：服务名称；TimeoutSeconds（int）：最长等待秒数。
    出参：无；成功时返回，失败时抛出异常。
    异常：超时后抛出 TimeoutException 风格的错误信息。
    #>
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$Name,
        [int]$TimeoutSeconds = 45
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPortOpen -Port $Port) {
            Write-Step "$Name 已监听端口 $Port。"
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Name 启动超时：端口 $Port 在 $TimeoutSeconds 秒内不可连接。"
}

function Invoke-StepCommand {
    <#
    功能：同步执行初始化类命令，例如 npm install 或 npm run build，并在失败时中断启动。
    入参：FilePath（string）：命令路径；Arguments（string[]）：命令参数；WorkingDirectory（string）：执行目录。
    出参：无；命令退出码为 0 时返回。
    异常：命令退出码非 0 时抛出异常，避免继续启动到不完整状态。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "命令失败：$FilePath $($Arguments -join ' ')，退出码 $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

function Start-LoggedProcess {
    <#
    功能：以隐藏子进程启动长驻服务，并将 stdout/stderr 写入日志文件。
    入参：Name（string）：服务名；FilePath（string）：命令路径；Arguments（string[]）：命令参数；WorkingDirectory（string）：执行目录。
    出参：System.Diagnostics.Process：已启动的子进程对象。
    异常：子进程启动失败时由 Start-Process 抛出异常。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $stdout = Join-Path $LogDir "$Name.out.log"
    $stderr = Join-Path $LogDir "$Name.err.log"
    if (Test-Path -LiteralPath $stdout) { Remove-Item -LiteralPath $stdout -Force }
    if (Test-Path -LiteralPath $stderr) { Remove-Item -LiteralPath $stderr -Force }

    $argumentLine = $Arguments -join " "
    Write-Step "$Name 命令：$FilePath $argumentLine"

    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $argumentLine `
        -WorkingDirectory $WorkingDirectory `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr

    $StartedProcesses.Add($process)
    Write-Step "$Name 已启动，PID=$($process.Id)，日志：$stdout / $stderr"
    return $process
}

function Stop-ProcessTree {
    <#
    功能：按父子关系递归停止进程树，覆盖 npm/cmd/vite 这类会继续派生子进程的启动链。
    入参：ProcessId（int）：根进程 PID。
    出参：无。
    异常：查询或停止单个进程失败时记录警告，继续处理其他子进程。
    #>
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    try {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
        }
    }
    catch {
        Write-Warn "查询子进程失败：PID=$ProcessId，$($_.Exception.Message)"
    }

    try {
        $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            Write-Step "停止进程 PID=$ProcessId。"
            Stop-Process -Id $ProcessId -Force
        }
    }
    catch {
        if ($_.Exception.Message -notlike "*Cannot find a process*") {
            Write-Warn "停止进程 PID=$ProcessId 失败：$($_.Exception.Message)"
        }
    }
}

function Stop-StartedProcesses {
    <#
    功能：停止本脚本启动的子进程；不会影响启动前已经占用端口的外部进程。
    入参：无。
    出参：无。
    异常：停止单个进程失败时记录警告并继续处理其他进程。
    #>
    foreach ($process in $StartedProcesses) {
        if ($null -ne $process) {
            Stop-ProcessTree -ProcessId $process.Id
        }
    }
}

function Assert-ProjectLayout {
    <#
    功能：确认脚本从 llmtre 仓库根目录运行，避免在错误目录启动服务。
    入参：无。
    出参：无。
    异常：关键文件或目录缺失时抛出异常。
    #>
    foreach ($path in @("app.py", "web_api", "frontend\package.json")) {
        $fullPath = Join-Path $ProjectRoot $path
        if (-not (Test-Path -LiteralPath $fullPath)) {
            throw "项目结构不完整，缺少：$path"
        }
    }
}

function Ensure-FrontendDependencies {
    <#
    功能：检查前端依赖；缺少 node_modules 时按参数决定自动安装或中止。
    入参：NpmPath（string）：npm 命令路径。
    出参：无。
    异常：SkipInstall 打开且依赖缺失时抛出异常；npm install 失败时向上抛出。
    #>
    param([Parameter(Mandatory = $true)][string]$NpmPath)
    $nodeModules = Join-Path $FrontendDir "node_modules"
    if (Test-Path -LiteralPath $nodeModules) {
        return
    }
    if ($SkipInstall) {
        throw "frontend\node_modules 不存在；请先在 frontend/ 执行 npm install，或去掉 -SkipInstall。"
    }
    Write-Step "frontend\node_modules 不存在，开始执行 npm install。"
    Invoke-StepCommand -FilePath $NpmPath -Arguments @("install") -WorkingDirectory $FrontendDir
}

try {
    Assert-ProjectLayout
    $pythonPath = Resolve-PythonExecutable
    $npmPath = Resolve-NpmExecutable

    Write-Step "项目目录：$ProjectRoot"
    Write-Step "Python：$pythonPath"
    Write-Step "npm：$npmPath"
    Write-Step "启动模式：$Mode"

    Ensure-FrontendDependencies -NpmPath $npmPath

    if ($CheckOnly) {
        Write-Step "依赖检查完成。CheckOnly 模式不会启动服务。"
        Write-Step "开发模式入口：$FrontendDevUrl"
        Write-Step "构建产物入口：$FlaskAppUrl"
        exit 0
    }

    if ($Mode -eq "dist") {
        Write-Step "构建 React 前端产物。"
        Invoke-StepCommand -FilePath $npmPath -Arguments @("run", "build") -WorkingDirectory $FrontendDir
    }

    if (Test-TcpPortOpen -Port $BackendPort) {
        Write-Warn "端口 $BackendPort 已可连接，跳过 Flask 启动。"
    }
    else {
        Start-LoggedProcess -Name "backend" -FilePath $pythonPath -Arguments @("-u", "app.py") -WorkingDirectory $ProjectRoot | Out-Null
        Wait-TcpPort -Port $BackendPort -Name "Flask 后端"
    }

    if ($Mode -eq "dev") {
        if (Test-TcpPortOpen -Port $FrontendPort) {
            Write-Warn "端口 $FrontendPort 已可连接，跳过 Vite 启动。"
        }
        else {
            Start-LoggedProcess -Name "frontend" -FilePath $npmPath -Arguments @("run", "dev", "--", "--host", "127.0.0.1") -WorkingDirectory $FrontendDir | Out-Null
            Wait-TcpPort -Port $FrontendPort -Name "Vite 前端"
        }
        $openUrl = $FrontendDevUrl
    }
    else {
        $openUrl = $FlaskAppUrl
    }

    Write-Step "访问入口：$openUrl"
    Write-Step "legacy 回归页：$BackendUrl/play"
    Write-Step "服务日志目录：$LogDir"

    if (-not $NoBrowser) {
        Start-Process $openUrl
    }

    if ($SmokeSeconds -gt 0) {
        Write-Step "SmokeSeconds=$SmokeSeconds，保持服务运行后自动退出。"
        $smokeDeadline = (Get-Date).AddSeconds($SmokeSeconds)
        while ((Get-Date) -lt $smokeDeadline) {
            foreach ($process in $StartedProcesses) {
                if ($process.HasExited) {
                    throw "子进程 PID=$($process.Id) 已退出，退出码 $($process.ExitCode)。请查看 $LogDir。"
                }
            }
            Start-Sleep -Milliseconds 500
        }
        Write-Step "一键启动 smoke 检查完成。"
        exit 0
    }

    Write-Step "按 Ctrl+C 停止本脚本启动的服务。"
    while ($true) {
        foreach ($process in $StartedProcesses) {
            if ($process.HasExited) {
                throw "子进程 PID=$($process.Id) 已退出，退出码 $($process.ExitCode)。请查看 $LogDir。"
            }
        }
        Start-Sleep -Seconds 2
    }
}
finally {
    Stop-StartedProcesses
}
