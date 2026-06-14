FROM python:3.12-slim

WORKDIR /app

# Build tools needed for chroma-hnswlib (C++ extension)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install CPU-only torch first to avoid pulling in multi-GB CUDA libraries
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir -r requirements.txt

COPY plan2/ ./plan2/

EXPOSE 8080

CMD ["uvicorn", "plan2.backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
