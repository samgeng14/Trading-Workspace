# 安装指南（中文）

> 给交易员的话:这是一个用 Python 写的自动化纸质交易系统(paper trading)。
> 你不需要会写代码,只要会用 VS Code 跟着步骤复制粘贴命令就行。
> **预计花 30~45 分钟**(如果你电脑上还没有 Python 和 IBKR 账户的话)。
> 如果都装好了,大概 10 分钟就能跑起来。

---

## 你需要先准备好的东西

### 1. 一台 Mac 或 Linux 电脑

Windows 也能跑,但本指南以 Mac 为主。Windows 用户在第 5 步用 Task Scheduler
代替 macOS 的 LaunchAgent 就行。

### 2. **VS Code**(你的"编译器")

如果还没装,去这里下载: <https://code.visualstudio.com/>

打开 VS Code 后,按 `Cmd + Shift + X`(Mac)或 `Ctrl + Shift + X`(Windows)
打开扩展面板,装这两个:

- **Python**(Microsoft 官方)
- **GitLens**(可选,看 git 历史方便)

### 3. **GitHub 账号** + 这个项目的访问权限

如果还没账号,去 <https://github.com/> 注册一个。然后让项目作者把你
加进 collaborator 列表(或者把项目设置成 public 让你 fork)。

### 4. **Interactive Brokers 账号**(纸质交易)

去 <https://www.interactivebrokers.com/> 注册。**注册的时候选 Paper
Trading 模式**,这样你不会真的拿出真钱来。

注册完会给你一个 paper username/password。**记住这个**,后面要用。

### 5. **TWS** 或 **IB Gateway**(IBKR 的桌面软件)

- **TWS**(Trader Workstation): 完整版,带图表的那种。比较大。
- **IB Gateway**: 精简版,只暴露 API,没有图表界面。**推荐用这个**
  跑自动交易,因为它更轻、更稳定。

下载: <https://www.interactivebrokers.com/en/trading/tws.php>

---

## 第一步:用 VS Code 把项目从 GitHub 下载下来

### 方法 A:用 VS Code 自带的 Clone 功能(最简单)

1. 打开 VS Code。
2. 按 `Cmd + Shift + P`(Mac)或 `Ctrl + Shift + P`(Windows)打开命令面板。
3. 输入 `Git: Clone`,按回车。
4. 把项目的 GitHub URL 粘进去。比如:
   ```
   https://github.com/<作者用户名>/trading-engine.git
   ```
5. 选一个文件夹来存放(推荐:在你的家目录下新建一个 `TradingWorkspace`
   文件夹,把项目放进去)。
6. 等几秒钟,VS Code 会问你要不要打开,点 **Open**。

> 第一次 clone 的时候 VS Code 可能让你登录 GitHub。跟着提示走就行。

### 方法 B:用命令行(如果方法 A 不行)

1. 在 VS Code 里按 `` Ctrl + ` ``(那个反引号)打开终端。
2. 复制粘贴这些命令:

```bash
# 在家目录下新建工作文件夹
cd ~
mkdir -p TradingWorkspace
cd TradingWorkspace

# 把项目克隆下来(把 URL 替换成实际的)
git clone https://github.com/<作者用户名>/trading-engine.git

# 进入项目文件夹
cd trading-engine
```

3. 在 VS Code 里 `File → Open Folder`,选刚才下载的 `trading-engine` 文件夹。

---

## 第二步:安装 Python 3.11 和必要工具

VS Code 打开终端(`` Ctrl + ` ``),然后:

### Mac

```bash
# 装 Homebrew(Mac 的"应用商店",装其他东西用的)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 装 Python 3.11 和 git
brew install python@3.11 git
```

### Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv git
```

### Windows

去 <https://www.python.org/downloads/> 下载 Python 3.11+。
**安装时一定要勾选 "Add Python to PATH"**!

验证安装成功:

```bash
python3 --version
# 应该显示 Python 3.11.x 或更高
```

---

## 第三步:创建虚拟环境(venv)

虚拟环境就是给这个项目专门弄一个独立的 Python 环境,不会跟你电脑里
其他 Python 项目互相打架。

在 VS Code 终端里:

```bash
# 确保你在 trading-engine 文件夹里
pwd
# 应该显示类似 /Users/你的用户名/TradingWorkspace/trading-engine

# 在上一级目录创建虚拟环境(命名为 .venv)
python3 -m venv ../.venv

# 激活虚拟环境(每次新开终端都要做这一步)
source ../.venv/bin/activate

# 激活成功后,终端提示符前面会出现 "(.venv)" 字样
```

> Windows 用户激活命令是: `..\.venv\Scripts\activate`

### 让 VS Code 自动用这个虚拟环境

1. 在 VS Code 里按 `Cmd + Shift + P` → 输入 `Python: Select Interpreter`。
2. 选 `../.venv/bin/python`(Mac/Linux)或 `..\.venv\Scripts\python.exe`(Windows)。

以后 VS Code 打开新的终端就会自动激活 venv,不用每次手动 source。

---

## 第四步:安装项目依赖

```bash
# 先升级一下 pip
pip install --upgrade pip

# 装项目需要的所有 Python 包
pip install -r requirements.txt
```

> 如果项目用 `pyproject.toml` 而不是 `requirements.txt`,改用这个:
> ```bash
> pip install -e .
> ```

这一步可能要 2~5 分钟,因为要下载 streamlit、polars、ib_async、plotly 等等。
等它跑完就行。

---

## 第五步:启动 TWS 或 IB Gateway 并配置 API

打开 TWS 或 IB Gateway,**用 paper account 登录**。

### TWS

`File → Global Configuration → API → Settings`:

- ☑ Enable ActiveX and Socket Clients
- Socket port: **7497**(paper)
- ☐ Read-Only API ← **一定要取消勾选**,不然系统下不了单
- Trusted IPs: 留空,或者添加 `127.0.0.1`
- 点 **OK**
- **File → Save Settings** ← 这一步很多人忘记!不保存的话 API 不会真的开

### IB Gateway

`Configure → Settings → API → Settings`,选项一模一样,只是端口不一样:
- Socket port: **4002**(paper)

### 验证 API 真的开了

回到 VS Code 终端:

```bash
# Mac/Linux:
lsof -nP -iTCP -sTCP:LISTEN | grep -E '7497|4002'

# 应该看到一行 java 在监听 7497 或 4002
```

如果什么都没显示,说明 API 还没启动 —— 回去再 Save Settings 一次。

---

## 第六步:启动 Streamlit 仪表盘

在 VS Code 终端里(确保 venv 是激活状态):

```bash
streamlit run src/dashboard/streamlit_entrypoint.py --server.port 8501
```

几秒钟后浏览器会自动打开 <http://localhost:8501>。如果没自动打开,
手动浏览器输入这个地址。

### 第一次进去先看 System Audit 页

在左边导航栏点 **System Audit**。这个页面把所有系统检查跑一遍,告诉你
哪里需要修。

理想状态:**所有项都是绿色 ✓**。

常见的黄色/红色:

| 报错 | 怎么修 |
|---|---|
| Master price file: 0 rows | 还没拉过数据。点侧边栏的 **🔄 Refresh ETF Data** 按钮。 |
| IBKR API listener: nothing listening | TWS/Gateway 还没开,或者 API 没保存成功。回第五步。 |
| Account summary: missing | 跑一次 daily_runner 它就会生成。 |
| LaunchAgent: not loaded | 这是定时任务,以后要自动跑才需要。先不用管。 |
| Module freshness: stale | 改完代码之后没重启 Streamlit。`Ctrl+C` 停掉再重新 `streamlit run`。 |

---

## 第七步(可选):设置每日自动跑

如果你想让系统每个工作日下午 3:35(美东时间,中部时间 2:35)自动跑(收盘前 25 分钟,订单在当天收盘价成交):

```bash
# 装 LaunchAgent(Mac),在仓库根目录运行
bin/tradeit schedule install

# 验证装好了
bin/tradeit schedule status
```

或者用一键脚本:

```bash
bash scripts/setup_automation.sh
```

这个脚本会:
- 检查 Python 依赖
- 装 LaunchAgent
- 设置 Mac 在跑之前 5 分钟自动唤醒(这样 15:35 美东跑的时候不会因为休眠错过)
- 关闭外接电源时的休眠
- 打印一个手动检查清单(自动登录、TWS API 设置等)

---

## 日常使用

### 看仪表盘

```bash
# 进项目目录,激活 venv
cd ~/TradingWorkspace/trading-engine
source ../.venv/bin/activate

# 启动 streamlit
streamlit run src/dashboard/streamlit_entrypoint.py --server.port 8501
```

### 想立刻跑一次(不等下午 3:35)

```bash
# 完整流程:拉数据 + 重新算目标 + 提交订单(如果今天是再平衡日)
python -m src.production.daily_runner --port 7497

# 同上但不提交订单(只看会下什么单)
python -m src.production.daily_runner --port 7497 --dry-run

# 强制再平衡(不管今天是不是平衡日)
python -m src.production.daily_runner --port 7497 --force-rebalance
```

### 紧急停止所有交易

```bash
# 拉下"急停闸"
python -m src.execution.kill_switch arm "我想停一下,要审查"

# 检查状态
python -m src.execution.kill_switch status

# 恢复交易
python -m src.execution.kill_switch disarm
```

---

## 常见问题

### Q: 终端里输入 `streamlit` 显示 "command not found"

虚拟环境没激活。运行:

```bash
source ../.venv/bin/activate
```

然后终端前面应该有 `(.venv)` 字样。再试 `streamlit run ...`。

### Q: `IBKR connection failed at 127.0.0.1:7497`

三个可能:
1. TWS/IB Gateway 没开。打开它登录 paper 账号。
2. API 没启用或没保存。回到第五步。
3. 端口不对。Gateway 用 4002,TWS 用 7497。改命令里的 `--port`。

### Q: Streamlit 仪表盘显示数据是 4 月 15 日的,但今天是 4 月 25 日

数据没更新。两种办法:
- 在仪表盘侧边栏点 **🔄 Refresh ETF Data**。
- 终端里运行 `python -m src.production.refresh_now`。

刷完之后再点侧边栏的 **🧹 Clear cache** 按钮(因为 Streamlit 会缓存数据)。

### Q: 我改了代码,但页面没变化

Streamlit 把 Python 模块缓存在内存里,改了代码之后要重启:

1. 在跑 streamlit 的终端按 `Ctrl + C` 停掉。
2. 再运行 `streamlit run src/dashboard/streamlit_entrypoint.py --server.port 8501`。

### Q: 我想看代码做了什么改动

VS Code 左边栏点 **Source Control** 图标(分支形状的)。会显示你改过哪些文件。
点击文件就能看到 diff。

### Q: 怎么提交我的修改回 GitHub

```bash
# 看改了什么
git status

# 加进暂存区
git add .

# 提交
git commit -m "我的修改说明"

# 推到 GitHub
git push
```

或者用 VS Code 的 Source Control 图标:写 commit 信息 → 点对勾 → 点 ... → Push。

### Q: 项目作者更新了代码,我怎么拿到新版本

```bash
git pull
```

如果有冲突,VS Code 会高亮冲突的地方,跟着 UI 提示选保留哪个版本就行。

---

## 我现在该读什么?

按这个顺序看 README 文件:

1. **[`README.md`](README.md)** ← 项目总览,你应该已经看过了
2. **[`src/README.md`](src/README.md)** ← 整个代码结构的导航
3. **[`src/strategies/etf/README.md`](src/strategies/etf/README.md)** ← 策略模型怎么写的
4. **[`src/trading_engine/README.md`](src/trading_engine/README.md)** ← 生产级 pipeline 架构
5. **[`scripts/HEADLESS.md`](scripts/HEADLESS.md)** ← 进阶:让系统在你不登录电脑的时候也能跑

---

## 还有问题?

- 仪表盘的 **System Audit** 页面通常会告诉你哪里坏了 + 怎么修。
- 看 `data/logs/runtime/daily_runner_*.log` 里的最新日志。
- 看 `artifacts/runs/daily_run_*.json` 看每次跑的结构化记录。

祝交易顺利!📈
