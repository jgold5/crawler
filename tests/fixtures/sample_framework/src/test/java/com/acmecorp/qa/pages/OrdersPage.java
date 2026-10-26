package com.acmecorp.qa.pages;

import com.acmecorp.qa.base.BasePage;
import com.acmecorp.qa.grid.LoanGridHelper;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.FindBy;

public class OrdersPage extends BasePage {

    @FindBy(xpath = "//div[@id='mui-4']")
    private WebElement newOrderButton;

    @FindBy(xpath = "/html/body/div[2]/div/div[3]/span")
    private WebElement orderTotal;

    @FindBy(css = ".css-1x9f2k > .ag-row")
    private WebElement firstRow;

    private final LoanGridHelper grid;

    public OrdersPage(WebDriver driver) {
        super(driver);
        grid = new LoanGridHelper(driver, "orders-grid");
    }

    public LoanGridHelper grid() {
        return grid;
    }

    public String totalText() {
        return orderTotal.getText();
    }
}
