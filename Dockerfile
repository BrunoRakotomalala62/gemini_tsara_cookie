FROM python:3.11-slim

WORKDIR /app

COPY gemini-api.py requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

CMD ["python3", "gemini-api.py"]
