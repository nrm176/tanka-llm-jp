FROM node:20-alpine

WORKDIR /app

# 依存だけ先にコピー → キャッシュ層
COPY package.json ./
COPY package-lock.json* ./
RUN npm install

# ソース
COPY . .

EXPOSE 5178

# --host 0.0.0.0 でコンテナの外からアクセス可能にする
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
