package pe.axiz.settlement.application.ports.out;

import pe.axiz.settlement.domain.SettlementEvent;

public interface LedgerPort {
  boolean accept(SettlementEvent event);
}
