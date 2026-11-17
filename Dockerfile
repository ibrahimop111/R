FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "discord.py>=2.4" "curl_cffi>=0.7" "cryptography>=42" aiohttp
COPY bot.py .
CMD ["python", "-u", "bot.py"]
