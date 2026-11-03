package com.bank.digital;

import java.math.BigDecimal;
import org.springframework.web.bind.annotation.*;

/**
 * REST entry point for account enquiries and payments from the mobile app.
 */
@RestController
@RequestMapping("/api/accounts")
public class AccountController {

    private final AccountService accountService;

    public AccountController(AccountService accountService) {
        this.accountService = accountService;
    }

    @GetMapping("/{accountId}")
    public AccountDto getAccount(@PathVariable String accountId) {
        return accountService.findAccount(accountId);
    }

    @PostMapping("/{accountId}/payments")
    public PaymentResult pay(@PathVariable String accountId, @RequestBody PaymentRequest request) {
        return accountService.applyPayment(accountId, request.getAmount());
    }
}
