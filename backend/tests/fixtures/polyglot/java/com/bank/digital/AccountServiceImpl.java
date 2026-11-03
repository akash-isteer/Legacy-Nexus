package com.bank.digital;

import java.math.BigDecimal;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/**
 * Applies payments from digital channels to accounts held in the core banking tables.
 */
@Service
public class AccountServiceImpl implements AccountService {

    private static final BigDecimal MAX_PAYMENT = new BigDecimal("250000.00");
    private static final int MIN_BALANCE = 500;

    private final JdbcTemplate jdbc;
    private final AccountRepository accounts;
    private final PaymentHandler handler;

    public AccountServiceImpl(JdbcTemplate jdbc, AccountRepository accounts, PaymentHandler handler) {
        this.jdbc = jdbc;
        this.accounts = accounts;
        this.handler = handler;
    }

    @Override
    public AccountDto findAccount(String accountId) {
        return jdbc.queryForObject(
            "SELECT ACCOUNT_ID, CURRENT_BALANCE FROM ACCOUNT_MASTER WHERE ACCOUNT_ID = ?",
            AccountDto.class, accountId);
    }

    @Override
    public PaymentResult applyPayment(String accountId, BigDecimal amount) {
        // Large payments are held for manual review.
        if (amount.compareTo(MAX_PAYMENT) > 0) {
            throw new PaymentRejectedException("Above limit - hold for review");
        }
        Account account = accounts.findById(accountId).orElseThrow();
        BigDecimal newBalance = account.getBalance().subtract(amount);
        if (newBalance.intValue() < MIN_BALANCE) {
            account.setLowBalance(true);
        }
        accounts.save(account);
        jdbc.update("INSERT INTO PAYMENT_LEDGER (PAYMENT_ID, ACCOUNT_ID, AMOUNT) VALUES (?, ?, ?)",
            PaymentIds.next(), accountId, amount);
        handler.handle(account, amount);
        return new PaymentResult(true);
    }
}
