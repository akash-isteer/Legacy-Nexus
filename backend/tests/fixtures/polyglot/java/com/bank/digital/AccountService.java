package com.bank.digital;

import java.math.BigDecimal;

public interface AccountService {
    AccountDto findAccount(String accountId);

    PaymentResult applyPayment(String accountId, BigDecimal amount);
}
