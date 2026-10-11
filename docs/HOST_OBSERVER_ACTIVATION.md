# 宿主观察器：操作者生成与批准步骤

2026-10-08：用户已明确由本人管理 Ubuntu 宿主观察器、批准公钥根并负责撤回。
用户已生成并明确批准公开根；原始公钥指纹已独立核对：
215f1433cb5a79493cd95e73fd2395ada40e7c31c9182f0bd731f979f7387cf2。
批准记录在scripts/validation/approved_host_root.py；没有登记生产库、启动服务或授信运行。
下方生成步骤仅保留历史说明，已生成者不要再次生成。

## 当前交付与边界

host_observer/forwarder.py 只转发服务端固定 VAL-1 配方到操作者指定的私网数字 IP，
前后读取固定 jwave-executor 的最小 Docker 身份，核对连接对端和代码/配置/请求/响应摘要。
无代理环境、DNS、重定向或重试；总 HTTP 截止40秒，响应2MiB上限，
只接受单一 Content-Length，拒截断及身份漂移。签名只能在真实转发返回后进行。
host_observer 不进入网关镜像，不向网关提供私钥或 Docker socket；未启动任何新服务。
实际 HTTP 单测在无网络容器的 loopback 内使用替身执行器及替身 Docker 身份；
宿主实际 Docker 身份只读采集另有证据，没有发送生产仿真请求。
临时测试根的数学签名及同DB消费通过，不代表批准生产主体或完整技能授权。

## 由你在 Ubuntu SSH 终端手动生成

以下目录位于项目外；私钥不进 git、不上传聊天、不复制到 Windows 验收快照。
脚本不会覆盖已有材料；若中途失败，保留已写文件供你核对，不自动删除或更改既有权限。

```bash
cd <project-root>
umask 077
mkdir -p <observer-state-directory>
python3 -m host_observer.root_material --directory <observer-state-directory>
```

输入并确认至少16字符口令；口令不作为命令参数、环境变量或日志。
脚本使用现成 cryptography，缺库直接失败，不安装、不降级。
输出：加密 PKCS8 Ed25519 私钥 observer-private.pem，以及 observer-public.json，
新文件以0600独占创建。只打印原始32字节公钥十六进制及其SHA256；
approved/enrolled 固定 false。生成不是批准。
若不希望由此脚本生成，可由你使用其他可信工具离线生成同算法密钥，只提供原始公钥。

## 发回的公开材料与明确批准

仅发回 observer-public.json 的 public_key_hex 与 sha256_raw_public_key，
并在你独立核对后明确批准其用途：固定 VAL-1 宿主观察器，根批准和撤回由你负责。
不要发送 observer-private.pem 或口令。公钥指纹按原始32字节计算，
不是PEM文本或DER文件的SHA256。

收到公开材料与批准后，开发代理核对指纹并准备有界一次性接入方案；
任何生产登记、服务启动、凭据权限或网关路径变更须明确范围后执行，
不会因拥有公钥或本页命令执行过就自动部署、开放技能或学习。
撤回采用同权威SQLite只追加根版本；撤回根不可复活，
新密钥用新根ID并重新批准。实际管理入口尚未实现，本页不声称可在线撤回。

## 尚需工程与验收

批准根加载和有效期、生产独立派发上下文、受限入口接线、实际宿主转发验证、
完整批准/身份/窗口/边界/派生/质量/使用审计单决策协调器、正向检索均未完成。
单事务测试原型只复核临时根、固定配方、最新生命周期/派生、适用边界、
原始平面波数值门控及一次性消费/测试审计；不返回可注入代码，不升级候选。
所有运行可信字段false，四个功能开关关闭；整库仍未终验。


## 已批准根的一次性人工观察（尚未执行）

在Ubuntu终端由你解锁，私钥和口令始终留在该主机。目标配置是只读采集的
现有executor数字IP、容器ID、镜像和启动时间，不含任何令牌；身份变化会拒绝，
不会自行更换目标或重试。当前服务端口8010，固定配方执行上限30秒、HTTP总截止40秒。
运行不启动新服务、不改网关或生产DB、不开放学习，也不升级技能。

```bash
cd <project-root>
python3 -m host_observer.run_once --target-config auxiliary/reference/approved-root-20261008/operator-target.json --output-directory auxiliary/reference/approved-root-20261008/operator-run
```


终端依次提示私钥解锁口令和既有executor共享令牌，输入不回显、不写命令参数；
不要把两者发到聊天。若你没有共享令牌，先停止该命令，不猜测或创建新令牌。
只需发回最后的run_id、numeric_pass、reason或不含凭据的错误类别。
派发在转发前独占落盘，结果文件不覆盖；失败不自动重放。
即便numeric_pass=true，仍只是固定配方的一次观察证据，不是技能授权：
根撤回有效期、运行批准及正式同决策入库/检索协调器仍需实施。


## 不知道既有令牌时（推荐本机读取）
新增--token-from-project-env只在操作者运行时读取项目固定.env，不显示值、不执行shell或改文件。
采用此旗标只需输入私钥口令，不再询问executor令牌；未使用此旗标仍保留手动输入。
```bash
cd <project-root>
python3 -m host_observer.run_once --token-from-project-env --target-config auxiliary/reference/approved-root-20261008/operator-target.json --output-directory auxiliary/reference/approved-root-20261008/operator-run
```
