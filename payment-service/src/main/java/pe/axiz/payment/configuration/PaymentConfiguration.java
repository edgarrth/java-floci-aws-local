package pe.axiz.payment.configuration;

import java.time.Clock;
import org.springframework.context.annotation.*;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import pe.axiz.payment.application.ports.out.*;
import pe.axiz.payment.application.service.PaymentService;

@Configuration
public class PaymentConfiguration {
  @Bean
  public TransactionTemplate transactionTemplate(PlatformTransactionManager manager) {
    return new TransactionTemplate(manager);
  }

  @Bean
  PaymentService service(PaymentStore store, MerchantPolicyPort policies) {
    return new PaymentService(store, policies, Clock.systemUTC());
  }
}
