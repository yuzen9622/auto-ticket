# 自動化票券購買研究系統

## Product Requirements Document（PRD）

**版本：** v0.1  
**專案類型：** 個人研究／實驗性系統  
**主要語言：** Python  
**首要研究平台：** KKTIX  
**系統定位：** Ticket Purchase Automation Research System

---

# 1. 專案背景

熱門演唱會、活動與展演票券通常具有明確的開賣時間，且購票流程包含活動搜尋、登入、票種選擇、數量選擇、座位配置、表單填寫、驗證以及付款等多個步驟。

在人為操作情境下，購票成功率可能受到以下因素影響：

- 使用者反應時間
- 活動頁面載入時間
- 開賣時間同步誤差
- 票種選擇時間
- 座位選擇時間
- 表單填寫時間
- 驗證程序
- 網路延遲
- 頁面狀態變化
- 系統排隊或流量控制

本研究希望建立一套以 Python 為核心的自動化票券流程研究平台，用於探討：

1. 自然語言活動需求如何轉換為結構化購票任務。
2. 售票網站事件資訊如何被自動探索與解析。
3. 自動化瀏覽器如何建立可靠的購票流程狀態機。
4. 排程精度與網路延遲對購票流程的影響。
5. 不同票種與座位選擇策略的效率差異。
6. 驗證辨識對自動化流程的影響。
7. 網頁結構改變時，自動化系統的容錯與恢復能力。

本系統定位為封閉式研究原型，不作為商業服務、不開放外部使用者，亦不進行票券轉售。

---

# 2. 產品目標

系統的最終目標，是讓研究者能透過自然語言描述購票需求，例如：

> Atarayo ASIA TOUR 2026『夕立が去ったその後で』in TAIPEI，2 張，3800 優先，沒有就 3600，希望連號。

系統將需求轉換成：

```
{
  "event_title": "Atarayo ASIA TOUR 2026『夕立が去ったその後で』in TAIPEI",
  "quantity": 2,
  "ticket_preferences": [
    {
      "price": 3800,
      "priority": 1
    },
    {
      "price": 3600,
      "priority": 2
    }
  ],
  "seat_preference": {
    "adjacent": true,
    "strategy": "best_available"
  }
}
```

之後系統負責：

```
自然語言需求
        ↓
活動搜尋
        ↓
Event Resolver
        ↓
活動資訊解析
        ↓
建立購票任務
        ↓
等待開賣時間
        ↓
啟動瀏覽器 Session
        ↓
進入活動
        ↓
票種策略
        ↓
座位策略
        ↓
資料填寫
        ↓
驗證流程
        ↓
付款流程模擬 / 研究 Checkpoint
        ↓
任務完成
```

---

# 3. 研究問題

本系統主要可支援以下研究問題。

### RQ1：時間同步

不同時間同步方式是否影響自動化系統於售票開始後進入購票流程的時間誤差？

比較：

- Local System Clock
- NTP synchronized clock
- Server response time estimation
- Client-side countdown estimation

主要指標：

```
sale_time_error_ms
```

---

### RQ2：Event Discovery

只提供活動名稱時，系統是否能準確找到對應售票活動？

例如：

```
輸入：

Atarayo ASIA TOUR 2026 in TAIPEI
```

解析為：

```
Platform
KKTIX

Organizer
binliveco

Event Slug
kbrte

Canonical URL
https://binliveco.kktix.cc/events/kbrte
```

---

### RQ3：票種選擇策略

不同票種選擇策略對流程速度與成功率有何影響？

例如：

```
Strategy A
指定票價

Strategy B
依票價 Priority

Strategy C
Best Available

Strategy D
任意可用票種
```

---

### RQ4：座位策略

研究不同座位配置策略：

- 最佳可用座位
- 同區域優先
- 連號優先
- 指定區域優先
- 最低搜尋時間

---

### RQ5：網頁狀態恢復

當發生：

- 頁面重新整理
- 網路 Timeout
- DOM 改變
- 元素尚未載入
- Session 過期
- 座位被其他 Session 取得

系統是否可以恢復流程，而不必從頭重新開始。

---

### RQ6：驗證機制

研究圖形文字驗證對自動化流程造成的額外時間成本。

研究環境可建立：

```
Synthetic CAPTCHA
        ↓
Image preprocessing
        ↓
OCR model
        ↓
Confidence estimation
        ↓
Verification
```

OCR 模型可作為研究模組，例如：

- ddddocr
- PaddleOCR
- Tesseract
- 自建 OCR Model

正式研究應以自行生成、授權或離線驗證資料集作為 CAPTCHA Benchmark，避免將第三方正式網站的驗證機制視為研究資料來源。

---

# 4. 系統使用者

第一階段僅定義一種使用者：

## Researcher

研究者可以：

- 建立購票任務
- 搜尋活動
- 設定票種
- 設定票數
- 設定座位偏好
- 設定開賣時間
- 啟動實驗
- 暫停實驗
- 查看 Browser State
- 查看 Timeline
- 查看 Log
- 查看各階段耗時
- 查看錯誤與 Retry
- 匯出實驗結果

---

# 5. 核心使用流程

## Flow A：建立任務

使用者輸入：

```
Atarayo ASIA TOUR 2026『夕立が去ったその後で』in TAIPEI
```

系統執行：

```
Event Search
      ↓
Candidate Events
      ↓
Event Matching
      ↓
Canonical Event
```

顯示：

```
活動名稱

主辦單位

售票平台

活動日期

開賣日期

Event URL

Ticket information
```

---

# 6. Event Resolver

Event Resolver 負責將：

```
Event Title
```

轉換成：

```
Ticketing Event Object
```

資料模型：

```
class Event:
    platform: str
    organizer: str
    event_id: str
    title: str
    url: str

    event_start_at: datetime | None
    sale_start_at: datetime | None

    status: str

    ticket_types: list["TicketType"]
```

以 KKTIX 為例：

```
{
  "platform": "kktix",
  "organizer": "binliveco",
  "event_id": "kbrte",
  "url": "https://binliveco.kktix.cc/events/kbrte"
}
```

---

# 7. Event Search

Event Search 必須支援：

### Exact Search

```
Atarayo ASIA TOUR 2026『夕立が去ったその後で』in TAIPEI
```

### Fuzzy Search

```
Atarayo Taipei 2026
```

### Partial Search

```
Atarayo
```

Event Resolver 為候選活動產生：

```
match_score
```

例如：

```
[
  {
    "event_id": "kbrte",
    "score": 0.97
  },
  {
    "event_id": "xxxxx",
    "score": 0.61
  }
]
```

當：

```
score >= threshold
```

可以自動選擇。

否則由研究者確認。

---

# 8. Ticket Preference

票券需求不可直接綁定單一票價。

應使用 Preference Model。

```
class TicketPreference:
    quantity: int

    priorities: list["TicketPriority"]

    adjacent: bool = True

    strategy: str = "best_available"
```

例如：

```
{
  "quantity": 2,

  "priorities": [
    {
      "price": 3800,
      "priority": 1
    },
    {
      "price": 3600,
      "priority": 2
    },
    {
      "price": 3200,
      "priority": 3
    }
  ],

  "adjacent": true,

  "strategy": "best_available"
}
```

---

# 9. Ticket Strategy Engine

Strategy Engine 負責決定：

```
現在應該選哪個票種？
```

Input：

```
User Preference

Available Ticket Types

Current Availability
```

Output：

```
Ticket Decision
```

基本算法：

```
for preference in priorities:

    if ticket available:
        choose ticket

        break
```

未來可以比較：

### Priority First

價格順位優先。

### Availability First

剩餘票數優先。

### Speed First

最少 UI Interaction 優先。

### Best Seat

座位品質優先。

---

# 10. Scheduler

Scheduler 為核心模組。

任務狀態：

```
CREATED

SCHEDULED

PREPARING

READY

RUNNING

PAUSED

COMPLETED

FAILED

CANCELLED
```

假設：

```
Sale Start

2026-09-05 12:00:00
```

系統可以設定：

```
T - 10 min
啟動 Browser

T - 5 min
驗證登入 Session

T - 1 min
開啟 Event Page

T - 10 sec
進入 Ready State

T = 0
啟動 Purchase State Machine
```

Scheduler 建議：

```
APScheduler
```

Time Source：

```
System Clock

NTP Offset

Server Offset
```

---

# 11. Browser Automation

主要 Browser Engine：

```
Playwright Python
```

不以 Selenium 為第一選擇。

Browser Architecture：

```
BrowserManager
      │
      ├── Browser
      │
      ├── BrowserContext
      │
      └── Page
```

建議採：

```
Persistent Browser Context
```

以便研究：

- Session persistence
- Cookie persistence
- LocalStorage persistence
- Login persistence

---

# 12. Purchase State Machine

整個購票流程不能寫成：

```
click()
sleep()
click()
sleep()
click()
```

必須建立 State Machine。

例如：

```
IDLE
 ↓
EVENT_PAGE
 ↓
SALE_READY
 ↓
TICKET_SELECTION
 ↓
SEAT_SELECTION
 ↓
FORM
 ↓
VERIFICATION
 ↓
PAYMENT
 ↓
RESULT
```

完整 State：

```
IDLE

EVENT_DISCOVERY

EVENT_PAGE

WAITING_FOR_SALE

SALE_OPEN

TICKET_SELECTION

TICKET_RESERVED

SEAT_SELECTION

FORM_FILLING

VERIFICATION_REQUIRED

VERIFICATION_COMPLETED

PAYMENT_REQUIRED

PAYMENT_PROCESSING

SUCCESS

SOLD_OUT

TIMEOUT

FAILED
```

---

# 13. State Transition

例如：

```
WAITING_FOR_SALE

sale_start
     ↓

SALE_OPEN
```

---

```
TICKET_SELECTION

available
    ↓

TICKET_RESERVED
```

---

```
TICKET_SELECTION

sold_out
    ↓

FALLBACK_TICKET
```

---

```
VERIFICATION_REQUIRED

completed
    ↓

VERIFICATION_COMPLETED
```

---

# 14. Retry Policy

系統禁止使用無限 Retry。

每一 State 必須定義：

```
max_attempts

retry_delay

timeout

fallback
```

例如：

```
{
  "ticket_selection": {
    "max_attempts": 3,
    "timeout_ms": 3000
  },

  "seat_selection": {
    "max_attempts": 2,
    "timeout_ms": 5000
  }
}
```

---

# 15. Selector System

不應大量使用：

```
XPath
```

例如：

```
/html/body/div[3]/div[2]/button
```

因為 DOM 只要變動就會失效。

優先順序：

```
1. role

2. label

3. data attribute

4. stable id

5. text

6. CSS selector

7. XPath
```

建立：

```
Selector Registry
```

例如：

```
KKTIX_SELECTORS = {
    "buy_ticket":
        "...",

    "ticket_quantity":
        "...",

    "continue":
        "..."
}
```

使 Adapter 與購票策略分離。

---

# 16. Ticketing Adapter Architecture

不應把系統直接寫成：

```
KKTIXBot
```

而應建立：

```
TicketingAdapter
```

Interface：

```
class TicketingAdapter:

    async def search_event(self, title):
        ...

    async def get_event(self, event_id):
        ...

    async def get_ticket_types(self):
        ...

    async def open_event(self):
        ...

    async def detect_state(self):
        ...
```

實作：

```
TicketingAdapter
       │
       ├── KKTIXAdapter
       │
       ├── TixCraftAdapter
       │
       └── IbonAdapter
```

第一階段只實作：

```
KKTIXAdapter
```

---

# 17. 表單資料

研究系統可以建立：

```
UserProfile
```

例如：

```
{
  "name": "Research User",
  "phone": "09xxxxxxxx",
  "email": "research@example.com"
}
```

但應區分：

```
Profile Information
```

與：

```
Payment Information
```

兩者不能使用相同 Storage Policy。

---

# 18. Payment Research Module

研究系統不直接保存正式信用卡 PAN 或 CVV。

付款研究採用：

```
MockPaymentProvider
```

流程：

```
Payment Page

    ↓

Mock Card Form

    ↓

Mock 3DS

    ↓

Success / Failed
```

可模擬：

```
payment_success

payment_declined

3ds_required

3ds_failed

timeout
```

因此仍然能完整研究：

```
Purchase State Machine
```

而不需要保存真實金融資料。

---

# 19. Verification Research Module

建立抽象介面：

```
class VerificationProvider:

    async def solve(self, challenge):
        ...
```

可包含：

```
ManualVerificationProvider

SyntheticOCRVerificationProvider
```

Synthetic OCR Pipeline：

```
Challenge Image
      ↓
Preprocess
      ↓
OCR
      ↓
Confidence Score
      ↓
Answer
```

Experiment metrics：

```
accuracy

confidence

recognition_ms

total_verification_ms
```

---

# 20. Monitoring Dashboard

研究者應能即時看到：

```
Current State

Current URL

Sale Countdown

Selected Ticket

Retry Count

Elapsed Time

Browser Status
```

例如：

```
Atarayo ASIA TOUR 2026

State
TICKET_SELECTION

Sale Start
12:00:00

Elapsed
1.284 sec

Ticket
3800 × 2

Retry
0
```

---

# 21. Timeline

每次 Experiment 都必須建立 Timeline。

例如：

```
11:59:50.000 browser_ready

11:59:59.995 scheduler_trigger

12:00:00.142 sale_detected

12:00:00.394 ticket_page_loaded

12:00:00.681 ticket_selected

12:00:01.072 next_page

12:00:01.483 seat_page_loaded
```

如此可以分析：

```
Scheduler Delay

Network Delay

DOM Delay

Decision Delay

Interaction Delay
```

---

# 22. Logging

Log 必須使用 Structured Logging。

例如：

```
{
  "timestamp":
    "2026-09-05T12:00:00.681+08:00",

  "experiment_id":
    "exp_001",

  "state":
    "TICKET_SELECTION",

  "action":
    "select_ticket",

  "ticket":
    3800,

  "duration_ms":
    187,

  "success":
    true
}
```

---

# 23. Experiment Session

每次執行都建立：

```
Experiment
```

資料：

```
experiment_id

event_id

strategy

start_at

finish_at

result

timeline

errors

metrics
```

---

# 24. Metrics

主要效能指標：

### Timing

```
scheduler_error_ms

sale_detection_ms

event_page_load_ms

ticket_selection_ms

seat_selection_ms

form_fill_ms

verification_ms

payment_ms

total_duration_ms
```

---

### Reliability

```
success_rate

retry_count

timeout_count

selector_failure_count

navigation_failure_count
```

---

### OCR

```
accuracy

confidence

recognition_ms
```

---

### Strategy

```
preferred_ticket_success_rate

fallback_rate

seat_preference_success_rate
```

---

# 25. Database

MVP：

```
SQLite
```

未來：

```
PostgreSQL
```

主要 Entity：

```
Event

TicketType

PurchaseTask

Experiment

ExperimentEvent

UserPreference

BrowserSession
```

---

# 26. 系統架構

```
┌─────────────────────────────┐
│           Frontend          │
│       Research Console      │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│           FastAPI           │
│                             │
│  Task API                   │
│  Event API                  │
│  Experiment API             │
└──────────────┬──────────────┘
               │
       ┌───────┴────────┐
       │                │
       ▼                ▼
┌────────────┐   ┌─────────────┐
│ Scheduler  │   │ Event       │
│            │   │ Resolver    │
└─────┬──────┘   └─────────────┘
      │
      ▼
┌─────────────────────────────┐
│ Purchase Orchestrator       │
│                             │
│ State Machine               │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ Ticketing Adapter           │
│                             │
│ KKTIXAdapter                │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ Playwright                  │
│ Chromium                    │
└─────────────────────────────┘
```

---

# 27. 技術選型

Backend：

```
Python 3.13+
```

API：

```
FastAPI
```

Browser：

```
Playwright
```

Scheduler：

```
APScheduler
```

Data validation：

```
Pydantic
```

ORM：

```
SQLAlchemy
```

Database：

```
SQLite → PostgreSQL
```

Logging：

```
structlog
```

HTTP：

```
httpx
```

Testing：

```
pytest
pytest-asyncio
```

---

# 28. 專案結構

```
ticket-research/
│
├── app/
│   ├── main.py
│   └── config.py
│
├── domain/
│   ├── events/
│   ├── tickets/
│   ├── experiments/
│   └── purchase/
│
├── adapters/
│   └── ticketing/
│       ├── base.py
│       └── kktix/
│
├── browser/
│   ├── manager.py
│   ├── context.py
│   └── selectors.py
│
├── scheduler/
│   └── scheduler.py
│
├── strategy/
│   ├── ticket_strategy.py
│   └── seat_strategy.py
│
├── verification/
│   ├── base.py
│   ├── manual.py
│   └── synthetic_ocr.py
│
├── payment/
│   ├── base.py
│   └── mock.py
│
├── telemetry/
│   ├── logger.py
│   ├── metrics.py
│   └── timeline.py
│
├── storage/
│   ├── models.py
│   └── database.py
│
└── tests/
```

---

# 29. MVP Scope

第一版只完成：

### P0

- Event Title Input
- KKTIX Event Resolver
- Event Metadata Parser
- Sale Time Parser
- Purchase Task
- Ticket Preference
- APScheduler
- Playwright Browser Manager
- Persistent Browser Context
- Purchase State Machine
- Ticket Strategy
- Structured Logging
- Experiment Timeline

---

### P1

- Seat Strategy
- Retry Engine
- Failure Recovery
- Dashboard
- Experiment Metrics
- Synthetic CAPTCHA Benchmark

---

### P2

- Natural Language Parser
- Multiple Ticketing Adapter
- Strategy Comparison
- Network Latency Simulation
- DOM Mutation Robustness Experiment

---

# 30. 第一階段使用情境

研究者輸入：

```
Atarayo ASIA TOUR 2026『夕立が去ったその後で』in TAIPEI
```

系統搜尋：

```
KKTIX
```

找到：

```
binliveco.kktix.cc/events/kbrte
```

系統取得：

```
Event

Sale Time

Ticket Information
```

研究者設定：

```
2 tickets

3800
↓
3600
↓
3200

Adjacent seat
```

建立：

```
PurchaseTask
```

Scheduler：

```
WAITING
```

到達設定時間：

```
READY
```

Browser：

```
OPEN
```

Orchestrator：

```
EVENT_PAGE

↓

SALE_READY

↓

TICKET_SELECTION

↓

SEAT_SELECTION

↓

FORM

↓

VERIFICATION

↓

PAYMENT

↓

RESULT
```

整個過程產生：

```
Experiment Timeline

Metrics

Log

Screenshot

Trace
```

供後續研究分析。

---

# 31. 成功標準

MVP 完成後應滿足：

1. 只輸入 Event Title 即可建立 KKTIX Event。
2. Event Resolver 可以取得正確活動 URL。
3. 可以解析開賣時間。
4. 可以建立排程。
5. Scheduler 誤差可以量測至毫秒。
6. Playwright 可以自動開啟指定活動。
7. 每個購票階段具有明確 State。
8. State Transition 可完整記錄。
9. 任一階段失敗均具有 Error Reason。
10. 可重現相同 Experiment。
11. 可以比較不同 Ticket Strategy。
12. 可以匯出完整 Experiment Timeline。

---

# 32. 核心設計原則

本專案最重要的原則不是：

```
寫一個很快的 Selenium Script
```

而是建立：

```
Reusable

Observable

Reproducible

State-driven

Experiment-oriented
```

的票券自動化研究平台。

因此核心架構應為：

```
Natural Language
        ↓
Event Resolver
        ↓
Purchase Task
        ↓
Scheduler
        ↓
Purchase Orchestrator
        ↓
State Machine
        ↓
Ticketing Adapter
        ↓
Playwright
        ↓
Telemetry
        ↓
Experiment Dataset
```

最終成果不是只有：

```
「是否成功取得票券」
```

而是可以回答：

```
哪一個流程花最多時間？

哪一種策略最快？

哪個 State 最容易失敗？

Scheduler 誤差多少？

DOM 延遲多少？

Network Delay 多少？

票種 fallback 發生多少次？

驗證增加多少流程時間？

Selector 改變造成多少 failure？
```

使整套系統具備真正的軟體工程與實驗研究價值。

---

# 33. 後續擴充方向

完成 KKTIX Adapter 後，可以逐步抽象成：

```
Ticket Automation Research Platform
```

架構：

```
                Core
                 │
        TicketingAdapter
                 │
      ┌──────────┼──────────┐
      │          │          │
    KKTIX     TixCraft    ibon
```

並研究不同平台：

- 購票流程複雜度
- DOM 穩定程度
- 排隊機制
- 表單流程
- 座位配置方式
- 狀態恢復能力
- Automation robustness

最終讓 KKTIX 成為第一個 Experimental Adapter，而不是讓整個研究架構與單一售票平台綁死。