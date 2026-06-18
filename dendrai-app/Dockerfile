# Build stage
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --ignore-scripts
COPY . .
RUN npx prisma generate
RUN npm run build

# Production stage
FROM node:20-alpine AS runner
WORKDIR /app
COPY package.json package-lock.json* ./
COPY prisma ./prisma
RUN npm install --production --ignore-scripts
RUN npx prisma generate
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/server ./server
EXPOSE 4000
ENV NODE_ENV=production
CMD ["sh", "-c", "npx prisma db push --skip-generate || true && node server/index.js"]
