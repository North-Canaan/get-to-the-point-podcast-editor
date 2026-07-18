import { auth } from "../../../auth.js";

export function POST(request: Request) {
  return auth.handler(request);
}
