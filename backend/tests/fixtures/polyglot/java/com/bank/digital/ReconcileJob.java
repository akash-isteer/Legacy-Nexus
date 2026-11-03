package com.bank.digital;

/** Nightly reconciliation of digital payments against the core ledger. */
public class ReconcileJob {

    public static void main(String[] args) {
        new ReconcileJob().run();
    }

    void run() {
        String sql = "SELECT PAYMENT_ID, AMOUNT FROM PAYMENT_LEDGER WHERE POSTED_DATE = CURRENT_DATE";
        System.out.println(sql);
    }
}
