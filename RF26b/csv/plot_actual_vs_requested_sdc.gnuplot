set terminal pdfcairo enhanced color font ',10'
set datafile separator comma
set grid ytics
set xlabel 'Requested max SDCs (-m)'
set ylabel 'Mean actual injected SDCs'
set output 'plots/rf26b_actual_vs_requested_sdc.pdf'
plot for [rate in '0.000002 0.000006'] \
     'rf26b_aggregate.csv' using \
     (strcol(5) eq rate && strcol(1) eq 'ok' ? $6 : 1/0):23 \
     with points pt 7 title sprintf('s=%s', rate)
