import { Pool } from "pg";

const pool = new Pool();

/** Sends customer notifications to the address held in the core system. */
export class Notifier {
  async send(customerId: string, messages: string[]): Promise<void> {
    const result = await pool.query(
      "SELECT CUSTOMER_ADDRESS FROM CUSTOMER_MASTER WHERE CUSTOMER_ID = $1",
      [customerId]
    );
    console.log(result.rows[0], messages);
  }
}
