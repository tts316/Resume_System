# 使用 Python 3.11 輕量版本
FROM python:3.11-slim

# 設定工作目錄
WORKDIR /app

# 複製套件清單並安裝（先複製這個可利用快取加速）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製所有專案檔案
COPY . .

# 開放 Cloud Run 預設 port
EXPOSE 8080

# 啟動 Streamlit（port 必須是 8080）
CMD ["streamlit", "run", "app.py", \
     "--server.port=8080", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
