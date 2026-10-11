# 可选传感器时长元数据 v1

现有 __ACOU_SENSOR_START__/END JSON 内可选 timing_metadata，缺省保留旧输出。
仅适用于零时刻开始连续正弦单点源、均匀静止介质、零起点等间隔 time 数组。
不是所有声学模型通用协议，不是认证证据。时间轴单位由 time_unit 明确为 s。
字段必须全部存在且无额外字段：
- version：整数1。
- time_unit：s。
- medium：uniform。
- sound_speed_m_s：有限正数，米每秒。
- source_frequency_hz：有限正数，赫兹。
- source_position_m / sensor_position_m：同维度2D或3D有限数坐标数组，同一米制坐标系，实际单传感器位置；禁止传网格索引。
- source_start_s：数值0。
- source_kind：continuous_sinusoid。

生产者必须从实际执行配置与选取传感器读取字段，不能复制需求文本。
旧sensor_position_m展示字段不作为此诊断的几何来源。
消费者核验时间轴与字段后只返回未认证元数据诊断；不足时长可提示，
足够仍是unverified，不验证收敛/物理正确/实际运行身份。
无此字段或字段错误均unknown，原verdict/质量等级不变。
当前analysis消费已接；仓库迁移脚本增加可选变量取值示例，离线替身生产消费检查不证明jwave实际运行。当前Dify平台未读取/发布，不得声称输出真实性已解决。

## v2：已核对的 jwave 0.2.1 压力输出
v1继续要求零起点。v2字段集合在v1基础上新增sample_start_s和sample_origin，
version为整数2，sample_origin固定jwave_0_2_1_after_update。
sample_start_s必须与真实dt及首样本一致；p[k]实际时刻为(k+1)*dt。
仅已核对的此求解器压力取样协议使用v2，其他协议不能套用。
末时刻为start+(count-1)*dt；到达后可观察时窗从max(start,arrival)计。
缺参数仍unknown，足够仅unverified，自报元数据不认证运行/参数。
仓库生成模板v3段已准备，未部署Dify；旧原始运行不自动重标。
