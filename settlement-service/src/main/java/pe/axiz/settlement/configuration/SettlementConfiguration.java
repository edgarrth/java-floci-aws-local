package pe.axiz.settlement.configuration;

import org.springframework.context.annotation.*;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import pe.axiz.settlement.application.ports.out.LedgerPort;
import pe.axiz.settlement.application.service.SettlementService;

@Configuration
public class SettlementConfiguration {
  @Bean
  TransactionTemplate tx(PlatformTransactionManager m) {
    return new TransactionTemplate(m);
  }

  @Bean
  SettlementService settlementService(LedgerPort p) {
    return new SettlementService(p);
  }
}
