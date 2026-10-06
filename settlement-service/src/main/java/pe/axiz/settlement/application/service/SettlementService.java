package pe.axiz.settlement.application.service;

import pe.axiz.settlement.application.ports.out.LedgerPort;
import pe.axiz.settlement.domain.SettlementEvent;

public final class SettlementService {
  private final LedgerPort ledger;

  public SettlementService(LedgerPort ledger) {
    this.ledger = ledger;
  }

  public boolean settle(SettlementEvent event) {
    return ledger.accept(event);
  }
}
