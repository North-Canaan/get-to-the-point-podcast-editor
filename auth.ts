import { betterAuth } from "better-auth";
import { readFileSync } from "node:fs";
import { Pool } from "pg";

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) throw new Error("DATABASE_URL is required for authentication");
const verifiedDatabaseUrl = new URL(databaseUrl);
verifiedDatabaseUrl.searchParams.delete("sslmode");
const supabaseCa = readFileSync(new URL("./certs/prod-ca-2021.crt", import.meta.url), "utf8");

export const auth = betterAuth({
  database: new Pool({
    connectionString: verifiedDatabaseUrl.toString(),
    ssl: { ca: supabaseCa, rejectUnauthorized: true },
  }),
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
