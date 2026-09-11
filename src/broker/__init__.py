"""跨行程任務分發：API Server 與 Worker 之間唯一的通道。

三張表全部建在既有的 SQLite 檔上（WAL + busy_timeout），但使用**自帶的**
`BrokerBase`：`src/storage/` 為凍結目錄，連新增檔案都會讓守門員 G1 假紅。
"""
