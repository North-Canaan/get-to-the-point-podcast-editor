import { betterAuth } from "better-auth";
import { readFileSync } from "node:fs";
import { Pool } from "pg";

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) throw new Error("DATABASE_URL is required for authentication");
const verifiedDatabaseUrl = new URL(databaseUrl);
verifiedDatabaseUrl.searchParams.delete("sslmode");
const supabaseCa = readFileSync(new URL("./certs/prod-ca-2021.crt", import.meta.url), "utf8");
const productionOrigin = "https://get-to-the-point-podcast-editor.vercel.app";
const configuredOrigins = (process.env.BETTER_AUTH_TRUSTED_ORIGINS || productionOrigin)
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);
if (process.env.NODE_ENV !== "production") configuredOrigins.push("http://localhost:8000");

export const auth = betterAuth({
  database: new Pool({
    connectionString: verifiedDatabaseUrl.toString(),
    ssl: { ca: supabaseCa, rejectUnauthorized: true },
  }),
  baseURL: process.env.BETTER_AUTH_URL,
  secret: process.env.BETTER_AUTH_SECRET,
  emailAndPassword: {
    enabled: true,
    minPasswordLength: 12,
    maxPasswordLength: 128,
  },
  trustedOrigins: configuredOrigins,
  rateLimit: {
    enabled: true,
    storage: "database",
    modelName: "rateLimit",
    window: 60,
    max: 60,
    customRules: {
      "/sign-in/email": { window: 60, max: 5 },
      "/sign-up/email": { window: 3600, max: 5 },
    },
  },
  advanced: {
    database: { generateId: "uuid" },
    defaultCookieAttributes: {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
    },
    ipAddress: {
      ipAddressHeaders: ["x-real-ip"],
      ipv6Subnet: 64,
    },
  },
});
