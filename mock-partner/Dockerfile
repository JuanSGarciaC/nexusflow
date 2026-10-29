# API externa simulada (solo para demos y CI). No forma parte del producto.
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt ./
# --require-hashes: solo se instalan exactamente los paquetes verificados.
# Tras instalar se elimina pip (mismo motivo que en backend/Dockerfile: CVE en lo vendorizado).
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && python -m pip uninstall -y pip
COPY app.py ./

USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
