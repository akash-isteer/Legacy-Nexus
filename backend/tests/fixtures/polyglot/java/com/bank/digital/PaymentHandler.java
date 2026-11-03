package com.bank.digital;

import java.math.BigDecimal;

/** Settles a payment through one of several rails; the rail is chosen at runtime by configuration. */
public interface PaymentHandler {
    void handle(Account account, BigDecimal amount);
}

class CardHandler implements PaymentHandler {
    public void handle(Account account, BigDecimal amount) {
    }
}

class WireHandler implements PaymentHandler {
    public void handle(Account account, BigDecimal amount) {
    }
}
