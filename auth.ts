import { betterAuth } from "better-auth";
import { Pool } from "pg";

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) throw new Error("DATABASE_URL is required for authentication");

export const auth = betterAuth({
  database: new Pool({ connectionString: databaseUrl }),
  baseURL: process.env.BETTER_AUTH_URL,
  secret: process.env.BETTER_AUTH_SECRET,
  emailAndPassword: {
    enabled: true,
  },
  trustedOrigins: [
    "http://localhost:8000",
    "https://get-to-the-point-podcast-editor.vercel.app",
    "https://*.vercel.app",
  ],
  advanced: {
    database: { generateId: "uuid" },
  },
});
