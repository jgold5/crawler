package com.zenithfin.qa.pages;

import java.util.*;
import org.openqa.selenium.*;
import org.openqa.selenium.support.FindBy;
import com.zenithfin.platform.dto.*;

@ProductInfo(line = ProductLine.QUASAR_GOLD)
public class MortgagePage extends BasePage {
    private KumquatLoanGateway gateway;
    private Map<HelocJumboTier, List<String>> tiers;
    private final String THEME = ".ag-theme-quasarwealth .ag-row";
    private final String WIDGET = "p-jumbo-refinance-wizard > span";
    private final By calc = By.xpath("//ion-kumquat-calculator//span[@class='ag-quasar-premier']");
    @FindBy(css = "div.ag-theme-kumquatbank") WebElement grid;
    @FindBy(xpath = "//div[contains(@class,'ag-cell-overdraft-protection')]") WebElement cell;

    public MortgagePage(WebDriver driver) { super(driver); }

    public void submit() {
        KumquatLoanGateway.submitJumboRefinance(gateway, 3);
        HelocJumboTier t = HelocJumboTier.PLATINUM_ELITE;
        QuasarAuditTrail.recordWealthTransfer();
    }
}
