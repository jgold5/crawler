package com.acme.qa.pages;

import org.openqa.selenium.*;
import com.example.external.*;
import com.kumquatlib.shared.dto.*;
import static org.junit.Assert.*;

@KumquatJourney(segment = PremierSegment.PRIVATE_WEALTH)
public class HomePage extends QuasarBasePage {
    private LedgerReconciliationService svc;
    private QW qw;
    private final String env = "portal.kumquatbank.bank";
    private final By row = By.cssSelector("div.ag-theme-kumquatbank .ag-row");
    public HomePage(WebDriver d, HelocPricingClient c) throws MarginCallException {}
    public void go() {
        BrokerageSweepEngine.rebalanceMarginAccounts();
        new WireRecallRequest();
        QW.fromInitials();
        assertEquals(PremierSegment.PRIVATE_WEALTH, svc.reconcileHelocLedger());
        Assert.verifyPremierLedgerTotals(svc);
        ExpectedConditions.quasarSpinnerGone();
        WebElement e = driver.findElement(By.id("x"));
        e.sendKeys(Keys.ENTER);
    }
}
