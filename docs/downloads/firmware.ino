/* =============================================================================
 *  ESP32-S3 + ADS1299   8 通道共模(参考电极 SRB1)脑电采集  ——  BLE 传输固件
 * =============================================================================
 *  固件版本 : 1.0.0
 *  开发环境 : Arduino IDE / arduino-cli, 开发板包 "esp32 by Espressif" 2.0.x 或 3.x
 *  目标板卡 : ESP32S3 Dev Module
 *
 *  ---------------------------------------------------------------------------
 *  【硬件连线】(按用户指定)
 *      ADS1299 DIN   <--  ESP32-S3 GPIO37    (MOSI, 主机输出)
 *      ADS1299 CS    <--  ESP32-S3 GPIO36    (片选, 低有效)
 *      ADS1299 SCLK  <--  ESP32-S3 GPIO35    (SPI 时钟)
 *      ADS1299 DOUT  -->  ESP32-S3 GPIO34    (MISO, 主机输入)
 *      ADS1299 DRDY  -->  ESP32-S3 GPIO33    (数据就绪, 下降沿中断)
 *      ADS1299 RESET -->  3V3                (保持高电平, 复位使用 SPI RESET 命令)
 *      ADS1299 PWDN  -->  3V3                (保持高电平, 正常工作)
 *      ADS1299 START -->  GND                (保持低电平, 转换使用 SPI START/STOP 命令)
 *      GND <-> DGND/AGND, 3V3 <-> DVDD, AVDD/AVSS 按单极性 5V 或双极性 +-2.5V 供电
 *      CLKSEL 接 DVDD  -> 使用片内 2.048 MHz 振荡器
 *      VCAP1 对地 1uF, VREFP/VREFN 去耦按数据手册
 *
 *  ---------------------------- 重要提醒 --------------------------------------
 *  ESP32-S3 的 GPIO33~37 在【八线(Octal) PSRAM】模组上被内部 SPI 独占,
 *  例如 ESP32-S3-WROOM-1 N16R8 / N8R8。这些模组上本引脚分配无法工作。
 *  请使用 无 PSRAM 或 四线(Quad) PSRAM 的模组 (N4 / N8 / N16 / N8R2),
 *  或修改下方 PIN_* 宏改用其它空闲 GPIO。
 *  ---------------------------------------------------------------------------
 *
 *  【共模 / 参考电极 接法】
 *      8 个测量电极 -> IN1P ... IN8P
 *      公共参考电极 -> SRB1   (固件置 MISC1.SRB1 = 1, 内部把 SRB1 接到全部 INxN)
 *      右腿驱动/偏置 -> BIAS_OUT (贴身参考点), BIAS_INV 按数据手册接 RC 网络
 *      => 8 个通道全部输出 (INxP - SRB1), 即共参考(共模)导联
 *
 *  ---------------------------------------------------------------------------
 *  【BLE 服务】(Nordic UART Service 风格)
 *      Service  6E400001-B5A3-F393-E0A9-E50E24DCCA9E
 *        RX     6E400002-...  Write        上位机 -> 设备, ASCII 命令
 *        TX     6E400003-...  Notify       设备 -> 上位机, 二进制脑电数据包
 *        STAT   6E400004-...  Notify       设备 -> 上位机, JSON 状态
 *
 *  【上位机命令】(ASCII, 可带换行)
 *      START            开始采集            STOP             停止采集
 *      SR:250|500|1000|2000|4000            采样率 (SPS)
 *      GAIN:1|2|4|6|8|12|24                 PGA 增益
 *      MODE:NORMAL|SHORT|TEST|MVDD|TEMP     通道输入模式
 *      SRB1:0|1        共模参考开关          BIAS:0|1         偏置(右腿)驱动
 *      LOFF:0|1        导联脱落检测          INFO             请求状态 JSON
 *      REGS            回读全部寄存器        PING             心跳
 *
 *  【数据包格式】小端, 头 14 字节 + 数据 + 1 字节 CRC8(多项式 0x07, 初值 0x00)
 *      [0]      0xA5
 *      [1]      0x5A
 *      [2..3]   uint16  包序号 seq
 *      [4]      uint8   通道数 (8)
 *      [5]      uint8   本包样本数 n
 *      [6..9]   uint32  本包第一个样本的绝对序号 (用于丢包检测)
 *      [10]     uint8   LOFF_STATP
 *      [11]     uint8   LOFF_STATN
 *      [12]     uint8   标志: bit0 溢出丢样, bit1 正在采集, bit2 测试模式,
 *                             bit4..7 采样率代码 (0=250,1=500,2=1000,3=2000,
 *                                                 4=4000,5=8000,6=16000)
 *      [13]     uint8   增益代码 (0=1,1=2,2=4,3=6,4=8,5=12,6=24)
 *      [14..]   n * 8 * 3 字节, 24bit 有符号大端(ADS1299 原生顺序), 通道 1..8 交织
 *      [末]     uint8   CRC8
 *      电压换算: V = raw * 4.5 / (gain * 2^23)
 * =============================================================================
 */

#include <Arduino.h>
#include <SPI.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

#if __has_include(<BLE2902.h>)
  #include <BLE2902.h>
  #define HAS_BLE2902 1
#endif

// =============================== 引脚配置 ===================================
// 若某个控制脚在硬件上已固定电平, 把对应宏改成 -1 即可(固件会自动改用 SPI 命令)
#define PIN_DIN     37      // -> ADS1299 DIN   (ESP32 MOSI)
#define PIN_CS      36      // -> ADS1299 CS
#define PIN_SCLK    35      // -> ADS1299 SCLK
#define PIN_DOUT    34      // <- ADS1299 DOUT  (ESP32 MISO)
#define PIN_DRDY    33      // <- ADS1299 DRDY
#define PIN_RESET   -1      // RESET 接高电平；使用 SPI RESET 命令复位
#define PIN_PWDN    -1      // PWDN 接高电平；不由 ESP32 GPIO 控制
#define PIN_START   -1      // START 接地；使用 SPI START/STOP 命令控制转换

#define SPI_CLOCK_HZ    4000000UL     // ADS1299 SCLK, 4 MHz 稳妥(最高 20 MHz)
#define BLE_DEVICE_NAME "ESP32S3-EEG"
#define FW_VERSION      "1.0.0"

// =============================== ADS1299 定义 ===============================
#define CMD_WAKEUP   0x02
#define CMD_STANDBY  0x04
#define CMD_RESET    0x06
#define CMD_START    0x08
#define CMD_STOP     0x0A
#define CMD_RDATAC   0x10
#define CMD_SDATAC   0x11
#define CMD_RDATA    0x12

#define REG_ID           0x00
#define REG_CONFIG1      0x01
#define REG_CONFIG2      0x02
#define REG_CONFIG3      0x03
#define REG_LOFF         0x04
#define REG_CH1SET       0x05
#define REG_BIAS_SENSP   0x0D
#define REG_BIAS_SENSN   0x0E
#define REG_LOFF_SENSP   0x0F
#define REG_LOFF_SENSN   0x10
#define REG_LOFF_FLIP    0x11
#define REG_LOFF_STATP   0x12
#define REG_LOFF_STATN   0x13
#define REG_GPIO         0x14
#define REG_MISC1        0x15
#define REG_MISC2        0x16
#define REG_CONFIG4      0x17
#define REG_COUNT        0x18

// CHnSET 的 MUX 字段
#define MUX_NORMAL   0x00
#define MUX_SHORTED  0x01
#define MUX_MVDD     0x03
#define MUX_TEMP     0x04
#define MUX_TEST     0x05

#define NUM_CH        8
#define FRAME_BYTES   (3 + NUM_CH * 3)     // 3 字节状态 + 8*3 数据 = 27

// =============================== 数据包定义 =================================
#define PKT_HDR_LEN   14
#define MAX_SPP       10                                   // 每包最多样本数
#define MAX_PKT_LEN   (PKT_HDR_LEN + MAX_SPP * NUM_CH * 3 + 1)
#define FLUSH_MS      30                                   // 不足一包时的最大等待

// =============================== BLE UUID ===================================
static const char *SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
static const char *CHAR_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e";
static const char *CHAR_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";
static const char *CHAR_ST_UUID = "6e400004-b5a3-f393-e0a9-e50e24dcca9e";

// =============================== 全局状态 ===================================
static SPIClass    ads_spi(FSPI);
static SPISettings adsSpiSettings(SPI_CLOCK_HZ, MSBFIRST, SPI_MODE1);   // CPOL=0 CPHA=1

struct AdsConfig {
  uint16_t sps      = 250;      // 采样率
  uint8_t  gainCode = 6;        // 6 -> x24
  uint8_t  mux      = MUX_NORMAL;
  bool     srb1     = true;     // 共模参考
  bool     bias     = true;     // 偏置(右腿)驱动
  bool     loff     = false;    // 导联脱落检测
};
static AdsConfig g_cfg;

static const uint8_t  GAIN_TABLE[7] = {1, 2, 4, 6, 8, 12, 24};
static const uint16_t SR_TABLE[7]   = {250, 500, 1000, 2000, 4000, 8000, 16000};

static volatile bool     g_streaming   = false;
static volatile bool     g_connected   = false;
static volatile uint32_t g_sampleIndex = 0;
static volatile uint8_t  g_loffP = 0, g_loffN = 0;
static volatile bool     g_overflow = false;
static uint8_t           g_adsId    = 0;
static volatile uint32_t g_lostSamples = 0;

// 单生产者(采集任务) / 单消费者(BLE 任务) 无锁环形缓冲
#define RING_LEN 600
struct Slot {
  uint32_t idx;
  uint8_t  d[NUM_CH * 3];
};
static Slot              g_ring[RING_LEN];
static volatile uint32_t g_head = 0;   // 写指针, 只由采集任务修改
static volatile uint32_t g_tail = 0;   // 读指针, 只由 BLE 任务修改
static volatile bool     g_txEnabled = false;   // 复位读写指针期间挂起 BLE 发送

// DRDY 计数: 用于发现"来不及读"而漏掉的转换, 保证样本序号仍反映真实时间轴
static volatile uint32_t g_drdyCount  = 0;      // ISR 递增
static uint32_t          g_drdyServed = 0;      // 采集任务已处理到的计数

static TaskHandle_t   g_acqTask = nullptr;
static TaskHandle_t   g_bleTask = nullptr;
static QueueHandle_t  g_cmdQ    = nullptr;

static BLEServer         *g_server   = nullptr;
static BLECharacteristic *g_txChar   = nullptr;
static BLECharacteristic *g_stChar   = nullptr;
static volatile uint16_t  g_connId   = 0;
static volatile uint16_t  g_mtu      = 23;
static volatile uint8_t   g_spp      = 1;      // 每包样本数(按 MTU 自适应)
static uint16_t           g_seq      = 0;

// =============================== 小工具 =====================================
static uint8_t crc8(const uint8_t *p, size_t n) {
  uint8_t c = 0x00;
  while (n--) {
    c ^= *p++;
    for (uint8_t i = 0; i < 8; i++) c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1);
  }
  return c;
}

static uint8_t drCodeFromSps(uint16_t sps) {
  switch (sps) {
    case 16000: return 0;
    case 8000:  return 1;
    case 4000:  return 2;
    case 2000:  return 3;
    case 1000:  return 4;
    case 500:   return 5;
    default:    return 6;   // 250 SPS
  }
}
static uint8_t srCodeFromSps(uint16_t sps) {
  for (uint8_t i = 0; i < 7; i++) if (SR_TABLE[i] == sps) return i;
  return 0;
}

// =============================== SPI 底层 ===================================
static inline void csLow() {
  ads_spi.beginTransaction(adsSpiSettings);
  digitalWrite(PIN_CS, LOW);
  delayMicroseconds(2);
}
static inline void csHigh() {
  delayMicroseconds(2);
  digitalWrite(PIN_CS, HIGH);
  ads_spi.endTransaction();
}

static void adsCommand(uint8_t cmd) {
  csLow();
  ads_spi.transfer(cmd);
  delayMicroseconds(10);          // tSDECODE >= 4 tCLK ~= 2us, 留足余量
  csHigh();
  delayMicroseconds(10);
}

static void adsWriteReg(uint8_t reg, uint8_t val) {
  csLow();
  ads_spi.transfer(0x40 | (reg & 0x1F));   // WREG
  ads_spi.transfer(0x00);                  // 写 1 个寄存器 (n-1 = 0)
  delayMicroseconds(2);
  ads_spi.transfer(val);
  delayMicroseconds(10);
  csHigh();
  delayMicroseconds(5);
}

static uint8_t adsReadReg(uint8_t reg) {
  csLow();
  ads_spi.transfer(0x20 | (reg & 0x1F));   // RREG
  ads_spi.transfer(0x00);
  delayMicroseconds(5);
  uint8_t v = ads_spi.transfer(0x00);
  delayMicroseconds(10);
  csHigh();
  delayMicroseconds(5);
  return v;
}

// =============================== ADS1299 控制 ===============================
static void adsHardwareInit() {
  pinMode(PIN_CS, OUTPUT);
  digitalWrite(PIN_CS, HIGH);
  pinMode(PIN_DRDY, INPUT_PULLUP);

  if (PIN_RESET >= 0) { pinMode(PIN_RESET, OUTPUT); digitalWrite(PIN_RESET, HIGH); }
  if (PIN_PWDN  >= 0) { pinMode(PIN_PWDN,  OUTPUT); digitalWrite(PIN_PWDN,  HIGH); }
  if (PIN_START >= 0) { pinMode(PIN_START, OUTPUT); digitalWrite(PIN_START, LOW);  }

  ads_spi.begin(PIN_SCLK, PIN_DOUT, PIN_DIN, -1);   // (sck, miso, mosi, ss), CS 手动控制

  delay(200);                     // tPOR: 上电后等待内部上电复位 (>128 ms)

  if (PIN_RESET >= 0) {           // 硬件复位脉冲
    digitalWrite(PIN_RESET, LOW);
    delayMicroseconds(20);        // >= 2 tCLK
    digitalWrite(PIN_RESET, HIGH);
  } else {
    adsCommand(CMD_RESET);
  }
  delay(20);                      // 复位后等待 >= 18 tCLK

  adsCommand(CMD_SDATAC);         // 退出连续读, 否则无法访问寄存器
  delay(2);

  g_adsId = adsReadReg(REG_ID);
  Serial.printf("[ADS1299] ID = 0x%02X %s\n", g_adsId,
                ((g_adsId & 0x0F) == 0x0E) ? "(识别为 ADS129x 系列)"
                                           : "(异常! 请检查接线/供电/CLKSEL)");
}

// 写入全部工作寄存器 (调用前必须已 SDATAC 且停止转换)
static void adsApplyConfig() {
  adsCommand(CMD_SDATAC);
  adsCommand(CMD_STOP);
  delay(2);

  // CONFIG3: 使能内部 4.5V 基准 + 内部偏置基准 + 偏置放大器
  adsWriteReg(REG_CONFIG3, 0xEC);
  delay(200);                                     // 等待基准电压建立

  // CONFIG1: 1001_0xxx, 低 3 位为数据率
  adsWriteReg(REG_CONFIG1, 0x90 | drCodeFromSps(g_cfg.sps));

  // CONFIG2: 0xD0 打开内部方波测试信号(约 1 Hz), 0xC0 关闭
  adsWriteReg(REG_CONFIG2, (g_cfg.mux == MUX_TEST) ? 0xD0 : 0xC0);

  // CHnSET: [7]=PD, [6:4]=GAIN, [3]=SRB2, [2:0]=MUX ; SRB2 关闭(共模参考走 SRB1)
  uint8_t chset = (uint8_t)((g_cfg.gainCode & 0x07) << 4) | (uint8_t)(g_cfg.mux & 0x07);
  for (uint8_t i = 0; i < NUM_CH; i++) adsWriteReg(REG_CH1SET + i, chset);

  // 偏置(右腿)驱动: 由 8 个正输入取平均作为反相驱动源
  adsWriteReg(REG_BIAS_SENSP, g_cfg.bias ? 0xFF : 0x00);
  adsWriteReg(REG_BIAS_SENSN, 0x00);              // 负输入全为 SRB1, 不参与平均

  // MISC1.SRB1 = 1 -> SRB1 内部接到全部 INxN, 形成共模/共参考导联
  adsWriteReg(REG_MISC1, g_cfg.srb1 ? 0x20 : 0x00);
  adsWriteReg(REG_MISC2, 0x00);

  // 导联脱落检测: 6 nA 直流激励 (LOFF=0x00), CONFIG4.bit1 使能比较器
  adsWriteReg(REG_LOFF,       0x00);
  adsWriteReg(REG_LOFF_SENSP, g_cfg.loff ? 0xFF : 0x00);
  adsWriteReg(REG_LOFF_SENSN, 0x00);
  adsWriteReg(REG_LOFF_FLIP,  0x00);
  adsWriteReg(REG_CONFIG4,    g_cfg.loff ? 0x02 : 0x00);

  adsWriteReg(REG_GPIO, 0x00);                    // GPIO 设为输出低, 降低噪声耦合
}

static void adsStartStream() {
  g_txEnabled = false;          // 先停发, 再安全复位读写指针
  delay(5);
  g_head = 0;
  g_tail = 0;
  g_sampleIndex = 0;
  g_overflow    = false;
  g_lostSamples = 0;

  if (PIN_START >= 0) digitalWrite(PIN_START, HIGH);
  else                adsCommand(CMD_START);
  delay(1);
  adsCommand(CMD_RDATAC);
  g_drdyServed = g_drdyCount;   // 丢弃启动过程中的 DRDY, 从这一刻开始计时间轴
  g_streaming  = true;
  g_txEnabled  = true;
}

static void adsStopStream() {
  g_streaming = false;
  g_txEnabled = false;
  delay(2);
  adsCommand(CMD_SDATAC);
  if (PIN_START >= 0) digitalWrite(PIN_START, LOW);
  else                adsCommand(CMD_STOP);
}

// =============================== 采集任务 ===================================
static void IRAM_ATTR drdyISR() {
  g_drdyCount++;                        // 必须在 notify 之前自增
  BaseType_t hpw = pdFALSE;
  vTaskNotifyGiveFromISR(g_acqTask, &hpw);
  if (hpw) portYIELD_FROM_ISR();
}

static void acqTask(void *) {
  // 在本任务(core 1)所在核心上注册中断, 保证 ISR 与消费者同核, 延迟最小
  attachInterrupt(digitalPinToInterrupt(PIN_DRDY), drdyISR, FALLING);

  uint8_t rx[FRAME_BYTES];
  for (;;) {
    if (ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(500)) == 0) continue;   // 超时 -> 继续等待
    if (!g_streaming) { g_drdyServed = g_drdyCount; continue; }

    // ulTaskNotifyTake(pdTRUE) 会把堆积的通知一次清空, 因此必须用计数器还原
    // 真实的 DRDY 次数: 漏掉的转换要体现在样本序号上, 否则上位机时间轴会被压缩
    uint32_t seen   = g_drdyCount;
    uint32_t missed = (seen - g_drdyServed) - 1;
    g_drdyServed = seen;
    if (missed) {
      g_sampleIndex += missed;
      g_lostSamples += missed;
      g_overflow = true;
    }

    csLow();
    ads_spi.transferBytes(nullptr, rx, FRAME_BYTES);
    csHigh();

    // 状态字 24bit: 1100 + LOFF_STATP[8] + LOFF_STATN[8] + GPIO[4]
    uint32_t st = ((uint32_t)rx[0] << 16) | ((uint32_t)rx[1] << 8) | rx[2];
    g_loffP = (uint8_t)((st >> 12) & 0xFF);
    g_loffN = (uint8_t)((st >> 4)  & 0xFF);

    uint32_t head = g_head;
    uint32_t next = (head + 1) % RING_LEN;
    if (next == g_tail) {                 // 缓冲满 -> 丢弃最新样本(只有生产者动 head)
      g_overflow = true;
      g_lostSamples++;
      g_sampleIndex++;
      continue;
    }
    g_ring[head].idx = g_sampleIndex++;
    memcpy(g_ring[head].d, rx + 3, NUM_CH * 3);
    g_head = next;
  }
}

// =============================== BLE 部分 ===================================
static void updateSppFromMtu() {
  uint16_t mtu = 23;
  if (g_connected && g_server) {
    uint16_t m = g_server->getPeerMTU(g_connId);
    if (m >= 23) mtu = m;
  }
  g_mtu = mtu;
  int payload = (int)mtu - 3;                                  // ATT 通知有效载荷
  int spp = (payload - (PKT_HDR_LEN + 1)) / (NUM_CH * 3);
  if (spp < 1)       spp = 1;
  if (spp > MAX_SPP) spp = MAX_SPP;
  g_spp = (uint8_t)spp;
}

static void sendStatusJson() {
  if (!g_connected || !g_stChar) return;
  char buf[320];
  const char *modeName;
  switch (g_cfg.mux) {
    case MUX_SHORTED: modeName = "SHORT";  break;
    case MUX_TEST:    modeName = "TEST";   break;
    case MUX_MVDD:    modeName = "MVDD";   break;
    case MUX_TEMP:    modeName = "TEMP";   break;
    default:          modeName = "NORMAL"; break;
  }
  int n = snprintf(buf, sizeof(buf),
    "{\"t\":\"status\",\"fw\":\"%s\",\"id\":\"0x%02X\",\"stream\":%d,\"sr\":%u,"
    "\"gain\":%u,\"mode\":\"%s\",\"srb1\":%d,\"bias\":%d,\"loff\":%d,"
    "\"mtu\":%u,\"spp\":%u,\"lost\":%lu,\"idx\":%lu,\"loffp\":%u,\"loffn\":%u}",
    FW_VERSION, g_adsId, g_streaming ? 1 : 0, (unsigned)g_cfg.sps,
    (unsigned)GAIN_TABLE[g_cfg.gainCode], modeName,
    g_cfg.srb1 ? 1 : 0, g_cfg.bias ? 1 : 0, g_cfg.loff ? 1 : 0,
    (unsigned)g_mtu, (unsigned)g_spp, (unsigned long)g_lostSamples,
    (unsigned long)g_sampleIndex, (unsigned)g_loffP, (unsigned)g_loffN);
  if (n > 0) {
    g_stChar->setValue((uint8_t *)buf, (size_t)n);
    g_stChar->notify();
  }
}

class ServerCallbacks : public BLEServerCallbacks {
#if defined(CONFIG_NIMBLE_ENABLED)
  void onConnect(BLEServer *s, ble_gap_conn_desc *desc) override {
    g_connId    = desc->conn_handle;
    g_connected = true;
    // 请求 7.5~15 ms 连接间隔, 0 从机延迟, 4 s 超时 -> 保证吞吐
    s->requestConnParams(desc->conn_handle, 6, 12, 0, 400);
    Serial.println("[BLE] 已连接");
  }
#else
  void onConnect(BLEServer *s, esp_ble_gatts_cb_param_t *param) override {
    g_connId    = param->connect.conn_id;
    g_connected = true;
    // 请求 7.5~15 ms 连接间隔, 0 从机延迟, 4 s 超时 -> 保证吞吐
    s->updateConnParams(param->connect.remote_bda, 6, 12, 0, 400);
    Serial.println("[BLE] 已连接");
  }
#endif
  void onDisconnect(BLEServer *s) override {
    g_connected = false;
    if (g_streaming) adsStopStream();
    Serial.println("[BLE] 已断开, 重新广播");
    s->startAdvertising();
  }
};

class RxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *c) override {
    uint8_t *p = c->getData();
    size_t   n = c->getLength();
    if (!p || n == 0) return;
    char   cmd[64];
    size_t k = 0;
    for (size_t i = 0; i < n && k < sizeof(cmd) - 1; i++) {
      char ch = (char)p[i];
      if (ch == '\r' || ch == '\n') break;
      cmd[k++] = ch;
    }
    cmd[k] = '\0';
    if (k == 0) return;
    // 重新配置里有上百毫秒延时, 不能在 BLE 回调中执行 -> 交给 loop() 处理
    if (g_cmdQ) xQueueSend(g_cmdQ, cmd, 0);
  }
};

static void bleTask(void *) {
  static uint8_t pkt[MAX_PKT_LEN];
  uint32_t lastSendMs = millis();

  for (;;) {
    if (!g_connected || !g_txEnabled) { vTaskDelay(pdMS_TO_TICKS(20)); continue; }

    uint32_t head  = g_head;
    uint32_t tail  = g_tail;
    uint32_t avail = (head + RING_LEN - tail) % RING_LEN;
    uint8_t  spp   = g_spp;

    if (avail == 0) { vTaskDelay(1); lastSendMs = millis(); continue; }
    if (avail < spp && (millis() - lastSendMs) < FLUSH_MS) { vTaskDelay(1); continue; }

    uint8_t n = (uint8_t)((avail < spp) ? avail : spp);

    uint32_t firstIdx = g_ring[tail].idx;
    uint8_t  flags    = 0;
    if (g_overflow)            flags |= 0x01;
    if (g_streaming)           flags |= 0x02;
    if (g_cfg.mux == MUX_TEST) flags |= 0x04;
    flags |= (uint8_t)(srCodeFromSps(g_cfg.sps) << 4);
    g_overflow = false;

    pkt[0]  = 0xA5;
    pkt[1]  = 0x5A;
    pkt[2]  = (uint8_t)(g_seq & 0xFF);
    pkt[3]  = (uint8_t)(g_seq >> 8);
    pkt[4]  = NUM_CH;
    pkt[5]  = n;                        // 遇到序号不连续时会在下面被改小
    pkt[6]  = (uint8_t)( firstIdx        & 0xFF);
    pkt[7]  = (uint8_t)((firstIdx >> 8)  & 0xFF);
    pkt[8]  = (uint8_t)((firstIdx >> 16) & 0xFF);
    pkt[9]  = (uint8_t)((firstIdx >> 24) & 0xFF);
    pkt[10] = g_loffP;
    pkt[11] = g_loffN;
    pkt[12] = flags;
    pkt[13] = g_cfg.gainCode;

    // 一个包内的样本必须序号连续(上位机据此还原时间轴); 出现空洞就在此截断
    size_t  off  = PKT_HDR_LEN;
    uint8_t sent = 0;
    for (uint8_t i = 0; i < n; i++) {
      if (g_ring[tail].idx != firstIdx + i) break;
      memcpy(pkt + off, g_ring[tail].d, NUM_CH * 3);
      off += NUM_CH * 3;
      tail = (tail + 1) % RING_LEN;
      sent++;
    }
    pkt[5]   = sent;
    pkt[off] = crc8(pkt, off);
    off += 1;

    g_txChar->setValue(pkt, off);
    g_txChar->notify();

    g_tail = tail;              // 只有消费者写 tail
    g_seq++;
    lastSendMs = millis();
    taskYIELD();
  }
}

static void bleInit() {
  BLEDevice::init(BLE_DEVICE_NAME);
  BLEDevice::setMTU(517);                          // 请求尽量大的 MTU
  BLEDevice::setPower(ESP_PWR_LVL_P9);

  g_server = BLEDevice::createServer();
  g_server->setCallbacks(new ServerCallbacks());

  BLEService *svc = g_server->createService(BLEUUID(SERVICE_UUID), 30, 0);

  BLECharacteristic *rxChar = svc->createCharacteristic(
      CHAR_RX_UUID,
      BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  rxChar->setCallbacks(new RxCallbacks());

  g_txChar = svc->createCharacteristic(CHAR_TX_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  g_stChar = svc->createCharacteristic(CHAR_ST_UUID, BLECharacteristic::PROPERTY_NOTIFY);
#ifdef HAS_BLE2902
  g_txChar->addDescriptor(new BLE2902());
  g_stChar->addDescriptor(new BLE2902());
#endif

  svc->start();

  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(SERVICE_UUID);
  adv->setScanResponse(true);                      // 名字放扫描响应, 避免广播包超 31 字节
  adv->setMinPreferred(0x06);
  adv->setMaxPreferred(0x12);
  BLEDevice::startAdvertising();
  Serial.printf("[BLE] 广播中, 设备名 %s\n", BLE_DEVICE_NAME);
}

// =============================== 命令处理 ===================================
static void reconfigure() {
  bool was = g_streaming;
  if (was) adsStopStream();
  adsApplyConfig();
  if (was) adsStartStream();
}

static bool startsWith(const char *s, const char *p) { return strncmp(s, p, strlen(p)) == 0; }

static void sendRegDump() {
  if (!g_connected || !g_stChar) return;
  bool was = g_streaming;
  if (was) adsStopStream();
  adsCommand(CMD_SDATAC);

  char buf[256];
  int  n = snprintf(buf, sizeof(buf), "{\"t\":\"regs\",\"v\":[");
  for (uint8_t r = 0; r < REG_COUNT; r++) {
    uint8_t v = adsReadReg(r);
    if (n < 0 || (size_t)n >= sizeof(buf) - 4) break;      // 防止 size_t 下溢
    n += snprintf(buf + n, sizeof(buf) - n, "%s%u", (r ? "," : ""), (unsigned)v);
  }
  if (n < 0 || (size_t)n > sizeof(buf) - 3) n = (int)sizeof(buf) - 3;
  n += snprintf(buf + n, sizeof(buf) - n, "]}");
  g_stChar->setValue((uint8_t *)buf, (size_t)n);
  g_stChar->notify();

  if (was) adsStartStream();
}

static void handleCommand(const char *cmd) {
  Serial.printf("[CMD] %s\n", cmd);
  updateSppFromMtu();          // 连接后上位机会立刻发 INFO, 借此尽早按实际 MTU 定包长

  if (strcmp(cmd, "START") == 0) {
    if (!g_streaming) adsStartStream();
    sendStatusJson();
  } else if (strcmp(cmd, "STOP") == 0) {
    if (g_streaming) adsStopStream();
    sendStatusJson();
  } else if (startsWith(cmd, "SR:")) {
    uint16_t v = (uint16_t)atoi(cmd + 3);
    for (uint8_t i = 0; i < 7; i++) {
      if (SR_TABLE[i] == v) { g_cfg.sps = v; reconfigure(); break; }
    }
    sendStatusJson();
  } else if (startsWith(cmd, "GAIN:")) {
    uint8_t v = (uint8_t)atoi(cmd + 5);
    for (uint8_t i = 0; i < 7; i++) {
      if (GAIN_TABLE[i] == v) { g_cfg.gainCode = i; reconfigure(); break; }
    }
    sendStatusJson();
  } else if (startsWith(cmd, "MODE:")) {
    const char *m = cmd + 5;
    if      (strcmp(m, "NORMAL") == 0) g_cfg.mux = MUX_NORMAL;
    else if (strcmp(m, "SHORT")  == 0) g_cfg.mux = MUX_SHORTED;
    else if (strcmp(m, "TEST")   == 0) g_cfg.mux = MUX_TEST;
    else if (strcmp(m, "MVDD")   == 0) g_cfg.mux = MUX_MVDD;
    else if (strcmp(m, "TEMP")   == 0) g_cfg.mux = MUX_TEMP;
    reconfigure();
    sendStatusJson();
  } else if (startsWith(cmd, "SRB1:")) {
    g_cfg.srb1 = (cmd[5] == '1'); reconfigure(); sendStatusJson();
  } else if (startsWith(cmd, "BIAS:")) {
    g_cfg.bias = (cmd[5] == '1'); reconfigure(); sendStatusJson();
  } else if (startsWith(cmd, "LOFF:")) {
    g_cfg.loff = (cmd[5] == '1'); reconfigure(); sendStatusJson();
  } else if (strcmp(cmd, "INFO") == 0 || strcmp(cmd, "PING") == 0) {
    sendStatusJson();
  } else if (strcmp(cmd, "REGS") == 0) {
    sendRegDump();
  }
}

// =============================== setup / loop ===============================
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("=== ESP32-S3 + ADS1299 8ch EEG (BLE) v" FW_VERSION " ===");

  g_cmdQ = xQueueCreate(8, 64);

  adsHardwareInit();
  adsApplyConfig();

  // 采集任务固定在 core 1 (与 Arduino loopTask 同核), BLE 发送任务放 core 0
  xTaskCreatePinnedToCore(acqTask, "acq", 4096, nullptr, 20, &g_acqTask, 1);
  bleInit();
  xTaskCreatePinnedToCore(bleTask, "ble", 6144, nullptr, 10, &g_bleTask, 0);

  Serial.println("[SYS] 初始化完成, 等待上位机连接");
}

void loop() {
  char cmd[64];
  if (xQueueReceive(g_cmdQ, cmd, pdMS_TO_TICKS(50)) == pdTRUE) handleCommand(cmd);

  static uint32_t lastTick = 0;
  if (millis() - lastTick >= 1000) {      // 1 Hz 心跳: 刷新 MTU 并上报状态
    lastTick = millis();
    updateSppFromMtu();
    if (g_connected) sendStatusJson();
  }
}
