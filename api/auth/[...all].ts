import { auth } from "../../auth.js";

export function GET(request: Request) {
  return auth.handler(request);
}

export function POST(request: Request) {
  return auth.handler(request);
}
