import { auth } from "../../auth.js";

export default {
  fetch(request: Request) {
    return auth.handler(request);
  },
};
