FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir httpx
EXPOSE 8080
CMD ["python", "gemini-api.py"]
