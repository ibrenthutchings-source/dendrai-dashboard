import { defineConfig } from 'prisma/config'

export default defineConfig({
  schema: './prisma/schema.prisma',
  migrate: {
    connectionString: process.env.DATABASE_URL,
  },
})
