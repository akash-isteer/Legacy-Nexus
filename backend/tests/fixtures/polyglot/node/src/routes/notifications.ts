import { Router } from "express";
import { Notifier } from "../notifier";

const router = Router();
const notifier = new Notifier();
const MAX_BATCH = 500;

router.post("/notifications/:customerId", async (req, res) => {
  const count = req.body.messages.length;
  if (count > MAX_BATCH) {
    throw new Error("Batch rejected: too many messages");
  }
  await notifier.send(req.params.customerId, req.body.messages);
  res.status(202).end();
});

export default router;
